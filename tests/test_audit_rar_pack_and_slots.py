"""The folder packer's two blind spots, which are the same blind spot.

`config.active_transfers` is the SEND side. A folder pack has no row there for
as long as it runs: check_queue_and_send() claims `rar_inprogress`, runs `rar`
for up to RAR_TIMEOUT (half an hour by default), and only appends to
active_transfers once the archive exists.

Two things read active_transfers and drew the wrong conclusion from that.

THE QUIESCE SAW AN IDLE BOT

wait_for_transfers_to_finish() polls active_transfers, so it returned True
immediately while a pack was mid-flight. The reload then re-executed
defaults.py - whose body contains `rar_inprogress = False` - and commands.py
cleared `user_processing_lock` outright, both while the packer was still
running. The next `!rar` read both interlocks as free and started a SECOND rar
process. Two packs of the same album target the same archive path, and the
second one removes the file the first is still writing.

THE CAPACITY CHECK WAS MINUTES STALE

The RAR branch checks MAX_DCC_SLOTS before starting rar, and appends its slot
after. For a large album those are minutes apart - long enough for every slot
to fill with plain file sends, each of which re-checked correctly on its own
way through. This was the one append that did not.
"""

import io
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402
import platform_compat  # noqa: E402
import runtime  # noqa: E402

from tests import support  # noqa: E402
from tests import test_path_security as security  # noqa: E402
from tests.support import DCCoreTestCase  # noqa: E402


class ARunningPackCountsAsBusy(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(active_transfers=[], rar_inprogress=False,
                        REHASH_TRANSFER_WAIT=60)
        self.addCleanup(lambda: setattr(config, "transfers_paused", False))

    def wait(self, sleep):
        return dcc.wait_for_transfers_to_finish(timeout=60, sleep=sleep,
                                                log=lambda _m: None)

    def test_the_wait_does_not_return_while_a_pack_runs(self):
        config.rar_inprogress = True
        ticks = []

        def tick(_seconds):
            ticks.append(1)
            if len(ticks) >= 3:
                config.rar_inprogress = False   # the pack finishes

        went_quiet = self.wait(tick)

        self.assertTrue(went_quiet)
        self.assertGreaterEqual(len(ticks), 3,
                                "the wait returned while a pack was running")

    def test_an_idle_bot_still_returns_at_once(self):
        """Control: the wait must not start blocking on a flag nobody set."""
        ticks = []

        went_quiet = self.wait(lambda _s: ticks.append(1))

        self.assertTrue(went_quiet)
        self.assertEqual(ticks, [])

    def test_a_pack_and_a_send_together_are_both_waited_for(self):
        config.rar_inprogress = True
        config.active_transfers.append({"user": "alice", "file": "a.flac"})
        ticks = []

        def tick(_seconds):
            ticks.append(1)
            if len(ticks) == 2:
                config.active_transfers.clear()
            if len(ticks) == 4:
                config.rar_inprogress = False

        self.assertTrue(self.wait(tick))
        self.assertGreaterEqual(len(ticks), 4)

    def test_the_pause_is_still_set_before_the_wait_begins(self):
        """Unchanged, and the thing the whole quiesce rests on: a send started
        during the wait is one the reload lands in the middle of."""
        config.rar_inprogress = True
        seen = []

        def tick(_seconds):
            seen.append(dcc.transfers_are_paused())
            config.rar_inprogress = False

        self.wait(tick)

        self.assertEqual(seen, [True])


class ThePackRechecksItsSlotWhenRarIsDone(security.PathSecurityBase):
    """The capacity test between the pack finishing and the slot being
    taken, evaluated: the real dispatcher and packer, with a stand-in for
    the rar binary that writes the archive and lets every slot fill while
    it runs. (Was a search of the source for the test's text, #1460.)"""

    def setUp(self):
        super().setUp()
        config.channel_users = {"#dccore-test": {"dave"}}
        self.set_config(MAX_DCC_SLOTS=2, rar_inprogress=False, transfers_paused=False,
                        bot_joined_channel=True)
        os.makedirs(config.TMP_ZIP_DIR, exist_ok=True)
        patch = mock.patch.object(dcc.time, "sleep", lambda *_a: None)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(platform_compat, "rar_command",
                                  lambda configured=None: os.path.join(self.tree.root, "rar"))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(setattr, runtime, "pack_owner", None)
        self.addCleanup(setattr, runtime, "pack_job", None)
        album = os.path.join(self.tree.music, "Some Artist", "Some Album")
        os.makedirs(album, exist_ok=True)
        with io.open(os.path.join(album, "01.flac"), "wb") as handle:
            handle.write(b"\x00" * 1024)
        self.row = {"file": "Some_Album.rar", "path": album, "channel": "#dccore-test",
                    "user_raw": "dave", "is_unpacked_rar_folder": True, "is_temporary_zip": True}
        self.slots_taken_while_rar_runs = 0

        class Packed:
            returncode = 0
            stdout = stderr = ""

        def rar_that_writes(cmd, *a, **kw):
            with io.open([c for c in cmd if c.endswith(".rar")][0], "wb") as handle:
                handle.write(b"Rar!")
            for n in range(self.slots_taken_while_rar_runs):
                config.active_transfers.append({"user": "other%d" % n, "file": "X.flac", "bytes_sent": 0})
            return Packed()

        support.fake_rar_runs(self, dcc.subprocess, rar_that_writes)

    def dispatch(self):
        config.dcc_queue["dave"] = [self.row]
        with security.quiet():
            dcc.check_queue_and_send(self.sock, "dave")

    def sends(self):
        return [args[1] for name, args in security.InlineThread.dispatched if name == "start_dcc_send"]

    def test_slots_filled_while_rar_ran_are_not_overshot(self):
        self.slots_taken_while_rar_runs = 2

        self.dispatch()

        self.assertEqual(len(config.active_transfers), 2, "the pack took a slot past MAX_DCC_SLOTS")
        self.assertEqual(self.sends(), [])
        self.assertEqual([r["file"] for r in config.dcc_queue["dave"]], ["Some_Album.rar"],
                         "the archive no longer waits in the queue")
        self.assertFalse(self.row["is_unpacked_rar_folder"], "the row does not name the archive")
        self.assertFalse(config.rar_inprogress, "the interlock was not released")

    def test_control_with_a_slot_left_the_archive_is_sent(self):
        self.slots_taken_while_rar_runs = 1

        self.dispatch()

        self.assertEqual(self.sends(), ["dave"])
        self.assertEqual(len(config.active_transfers), 2)


class ThePackRechecksItsSlotBeforeTakingOne(DCCoreTestCase):
    """Still asserted on the source. The capacity test itself is evaluated
    above (ThePackRechecksItsSlotWhenRarIsDone), with rar a stand-in; the
    checks left here could move onto the same fixture."""

    def source(self):
        """The packer first, then the wrapper that starts it: the packing has
        been pack_folder_for_request() since #1300, and the interlocks it
        hands back are still released in check_queue_and_send()'s
        inline_rar_packer(). Section B's sweep, with its own slot check, has
        been claim_next_in_line() since #1304."""
        import inspect

        return (inspect.getsource(dcc.pack_folder_for_request) + inspect.getsource(dcc.check_queue_and_send)
                + inspect.getsource(dcc.claim_next_in_line))

    def test_it_is_taken_under_the_queue_lock(self):
        """Two dispatch threads reaching this together would otherwise both
        see room for the last slot."""
        source = self.source()
        after_check = source.split("the packed archive stays queued")[0]

        self.assertIn("with queue_lock:", after_check)

    def test_a_refused_pack_releases_the_interlock_it_holds(self):
        """Returning without clearing rar_inprogress would stop every future
        pack for every user until the daemon restarted - the exact failure the
        RAR_TIMEOUT comment above it warns about. The release is the
        wrapper's finally, ONCE (#714): the refusal returns None to it rather
        than releasing itself and being released again."""
        source = self.source()
        refusal = source.split("the packed archive stays queued")[1][:400]

        self.assertNotIn("config.rar_inprogress = False", refusal, "released twice")
        self.assertIn("Released by the wrapper's finally, once", refusal)
        finally_block = source.split("if not handed_off:", 1)[1][:600]
        self.assertIn("config.rar_inprogress = False", finally_block)
        self.assertIn("user_processing_lock", finally_block)

    def test_a_refused_pack_lets_a_waiting_one_start(self):
        """redispatch_waiting_pack() is the only thing that revisits a user
        turned away at [RAR-HOLD]; without it they wait for a trigger that
        never comes. It is the wrapper's finally that calls it, once - for
        the nick the claim is held under now, which a rename may have changed
        since the pack started (#1268)."""
        source = self.source()
        finally_block = source.split("if not handed_off:", 1)[1][:600]

        self.assertIn("redispatch_waiting_pack(irc_sock, just_finished=holder)", finally_block)

    def test_the_pre_dispatch_checks_are_all_still_there(self):
        """Control on the control: this fix is about the ONE append that had
        no check, not about replacing the checks that were already right.

        Three of them - the RAR branch before it starts packing, the plain
        file path, and section B - all in the same form. The new one is a
        fourth, in a different form, because it has to hold the lock across
        the decision and the append."""
        source = self.source()

        self.assertEqual(
            source.count("if len(config.active_transfers) >= config.MAX_DCC_SLOTS:"),
            3)


if __name__ == "__main__":
    unittest.main()
