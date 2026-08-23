# update_list.py - Renodlad OmenServe-layout med dubbla listor (Del 1 av 2)
import os
import sys
import datetime
import zipfile
import time
import config
import zipfile

def format_size_human(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f}PB"

def format_total_size(bytes_size):
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f}PiB"

def _discard_temp_lists(*paths):
    """Remove half-written temporary lists so they cannot be mistaken for real ones."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as err:
            print(f"[LIST-CLEAN ERROR] Kunde inte ta bort {path}: {err}")


def _prune_superseded_lists(keep):
    """Delete older generated lists once the new ones are safely in place.

    Runs AFTER the swap, never before: the previous index has to stay usable for the whole
    scan, which can take minutes on a large NFS mount.
    """
    removed = 0
    try:
        entries = os.listdir(config.LOCAL_LIST_DIR)
    except OSError as err:
        print(f"[LIST-CLEAN ERROR] Kunde inte läsa {config.LOCAL_LIST_DIR}: {err}")
        return

    for item in entries:
        if item in keep:
            continue
        if not item.startswith(config.LIST_BASE_NAME):
            continue
        if not (item.endswith(".txt") or item.endswith(".zip")):
            continue
        try:
            os.remove(os.path.join(config.LOCAL_LIST_DIR, item))
            removed += 1
        except OSError as err:
            print(f"[LIST-CLEAN ERROR] Kunde inte ta bort {item}: {err}")

    if removed:
        print(f"[LIST-CLEAN] Tog bort {removed} ersatt(a) lista/listor.")


def generate_master_list():
    """Skannar musikmappen, rensar gamla filer först och bygger båda listorna samtidigt"""
    import os
    import sys
    import time
    import datetime
    import zipfile
    import re
    import config

    if not os.path.exists(config.LOCAL_LIST_DIR):
        os.makedirs(config.LOCAL_LIST_DIR)

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    txt_filename = f"{config.LIST_BASE_NAME}-{today}.txt"
    zip_filename = f"{config.LIST_BASE_NAME}-{today}.zip"
    rar_filename = f"{config.LIST_BASE_NAME}-RAR-{today}.txt"
    
    txt_path = os.path.join(config.LOCAL_LIST_DIR, txt_filename)
    zip_path = os.path.join(config.LOCAL_LIST_DIR, zip_filename)
    rar_path = os.path.join(config.LOCAL_LIST_DIR, rar_filename)
    
    SIZE_FILE_PATH = os.path.join(config.LOCAL_LIST_DIR, "flac-serv-size.txt")
    RAWBYTES_FILE_PATH = os.path.join(config.LOCAL_LIST_DIR, "flac-serv-rawbytes.txt")

    scan_start = time.time()

    # The old lists are NOT deleted here any more. This ran before the scan, so an NFS mount
    # that went away, a disk that filled, or any exception below left the daemon with no index
    # at all: find_latest_list() returns None, @find answers "No MasterList found" and the
    # advert publicly announces 0 files, until someone notices and runs a successful !update.
    #
    # The new lists are written to temporary names alongside the real ones and swapped in only
    # once the whole generation has succeeded (see finalise below). A failed run now leaves the
    # previous index exactly as it was.
    tmp_txt_path = txt_path + ".new"
    tmp_rar_path = rar_path + ".new"
    tmp_zip_path = zip_path + ".new"

    print(f"[LIST-GEN] Skannar biblioteket i {config.FILE_DIRECTORY}...")
    
    all_files_data = []
    total_bytes = 0

    for root, dirs, files in os.walk(config.FILE_DIRECTORY):
        # Spara alla sanna låtar under sin exakta och fullständiga disk-sökväg!
        for file in files:
            if file.lower().endswith(('.mp3', '.flac')):
                full_file_path = os.path.join(root, file)
                file_bytes = 0
                try:
                    file_bytes = os.path.getsize(full_file_path)
                    total_bytes += file_bytes
                except:
                    pass
                rel_dir = os.path.relpath(root, config.FILE_DIRECTORY)
                if rel_dir == ".":
                    rel_dir = ""
                all_files_data.append((rel_dir, file, file_bytes))

    # Sortera på sanna mappnamn och filnamn
    all_files_data.sort(key=lambda x: (str(x[0]).lower(), str(x[1]).lower()))

    total_files_count = len(all_files_data)
    scan_end = time.time()
    elapsed_seconds = scan_end - scan_start
    if elapsed_seconds <= 0:
        elapsed_seconds = 0.1

    files_per_second = int(total_files_count / elapsed_seconds)
    def format_total_size(b):
        for unit in ['B','KB','MB','GB','TB']:
            if b < 1024.0: return f"{b:.2f}{unit}"
            b /= 1024.0
        return f"{b:.2f}PB"
    def format_size_human(b):
        return format_total_size(b)

    formatted_size = format_total_size(total_bytes)
    
    time_struct = time.gmtime(elapsed_seconds)
    duration_str = time.strftime("%H:%M:%S", time_struct)

    day = datetime.datetime.now().day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    date_header_str = datetime.datetime.now().strftime(f"{day}{suffix} %b %Y")
    
    try:
        with open(tmp_txt_path, "w", encoding="utf-8") as f, \
             open(tmp_rar_path, "w", encoding="utf-8") as f_rar:
                 
            f.write(f"List of {total_files_count:,} Files ({formatted_size}) generated on {date_header_str} in {duration_str} ( {files_per_second:,} Files Per Second )\n")
            f.write(f"To request a file, copy/paste to the channel... !{config.NICKNAME} FILENAME eg. !{config.NICKNAME} Songname.flac\n\n\n")

            f_rar.write(f"List of Entire Album Folders (!rar) for !{config.NICKNAME} generated on {date_header_str}\n")
            f_rar.write(f"To request an entire album, copy/paste the line... eg. !{config.NICKNAME} !rar D:\\MUSIC\\Album\\\n")
            f_rar.write("="*90 + "\n\n")

            current_folder = None
            written_rar_folders = set() # Skyddar din !rar-lista från att få dubbelrader!

            for folder, filename, bytes_size in all_files_data:
                if folder != current_folder:
                    current_folder = folder
                    
                    # 🛡️ NORMAL LISTUTSKRIFT: Skriver den fullständiga undermappen (t.ex. \Digital Media 1\) till textlistan!
                    raw_folder_str = f"D:\\MUSIC\\{folder}\\" if folder else "D:\\MUSIC\\"
                    display_folder = raw_folder_str.replace("/", "\\")
                    
                    f.write(f"\n=====================================================\n")
                    f.write(f"{display_folder}\n")
                    f.write(f"=====================================================\n")
                    
                    # 🛡️ KIRURGISK RAR-TVÄTT: Tvättar bort multidisc-ändelser ENBART för !rar-albumlistan!
                    if folder:
                        rar_folder_clean = folder
                        lowered_rar = rar_folder_clean.lower()
                        for box_word in ['\\cd', '\\disc', '\\volume', '\\digital media', '\\media', '/cd', '/disc', '/volume', '/digital media', '/media']:
                            if box_word in lowered_rar:
                                idx = lowered_rar.find(box_word)
                                if idx != -1:
                                    rar_folder_clean = rar_folder_clean[:idx]
                                break
                                
                        raw_rar_str = f"D:\\MUSIC\\{rar_folder_clean}\\"
                        display_rar_folder = raw_rar_str.replace("/", "\\")
                        
                        # Skriv raden STRICT en gång per huvudalbum till .rar-textfilen!
                        if display_rar_folder not in written_rar_folders:
                            f_rar.write(f"!{config.NICKNAME} !rar {display_rar_folder}\n")
                            written_rar_folders.add(display_rar_folder)
                single_file_size = format_size_human(bytes_size)
                f.write(f"!{config.NICKNAME} {filename}  ::INFO:: {single_file_size}\n")

        print(f"[LIST-GEN] Textlista skapad utan problem: {tmp_txt_path}")
        print(f"[LIST-GEN] RAR-albumlista skapad utan problem: {tmp_rar_path}")
        
        with open(SIZE_FILE_PATH, "w", encoding="utf-8") as sf:
            sf.write(formatted_size)
            
        with open(RAWBYTES_FILE_PATH, "w", encoding="utf-8") as rbf:
            rbf.write(str(total_bytes))
            
        # A scan that found NOTHING is almost always an unavailable mount, not an empty
        # library - and publishing it would replace a good index with an empty one, which is
        # exactly the failure this rewrite exists to prevent. Size cannot be the test: the
        # file always carries two header lines, so it is never zero bytes.
        #
        # Accept an empty result only when there is no working index to lose, so a genuine
        # first run on an empty library still succeeds.
        if total_files_count == 0:
            try:
                existing = [f for f in os.listdir(config.LOCAL_LIST_DIR)
                            if f.startswith(config.LIST_BASE_NAME)
                            and f.endswith(".txt") and "-RAR-" not in f]
            except OSError:
                existing = []
            if existing:
                print("[LIST-GEN ERROR] Scan found 0 files but an index already exists "
                      "(mount unavailable?). Keeping the previous index.")
                _discard_temp_lists(tmp_txt_path, tmp_rar_path, tmp_zip_path)
                return False
            print("[LIST-GEN] Scan found 0 files and there is no previous index; "
                  "publishing an empty list.")

        if not os.path.exists(tmp_txt_path):
            print("[LIST-GEN ERROR] No list was written. Keeping the previous index.")
            _discard_temp_lists(tmp_txt_path, tmp_rar_path, tmp_zip_path)
            return False

        print(f"[LIST-GEN] Packar ner BADA textlistorna i master-zip-arkiv...")
        # Build the zip from the temp files but store them under their FINAL names, so the
        # archive users download is identical to what it always was.
        with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(tmp_txt_path, arcname=os.path.basename(txt_path))
            if os.path.exists(tmp_rar_path) and os.path.getsize(tmp_rar_path) > 0:
                zipf.write(tmp_rar_path, arcname=os.path.basename(rar_path))

        # Everything generated cleanly. Swap the new files in, THEN remove the superseded
        # ones. os.replace overwrites atomically on both POSIX and Windows, where os.rename
        # would raise because the destination already exists.
        os.replace(tmp_txt_path, txt_path)
        os.replace(tmp_rar_path, rar_path)
        os.replace(tmp_zip_path, zip_path)
        print(f"[LIST-GEN] Nya listor aktiverade: {os.path.basename(txt_path)}")

        _prune_superseded_lists(keep={os.path.basename(txt_path),
                                      os.path.basename(rar_path),
                                      os.path.basename(zip_path)})
        return True
            
    except Exception as e:
        print(f"[LIST-GEN ERROR] Misslyckades att generera listor: {e}")
        print("[LIST-GEN] Den tidigare listan lamnades ororld och ar fortfarande i bruk.")
        _discard_temp_lists(tmp_txt_path, tmp_rar_path, tmp_zip_path)
        return False

if __name__ == "__main__":
    print("--- Startar schemalagd veckouppdatering av fillistan ---")
    if not os.path.exists(config.FILE_DIRECTORY):
        print(f"[CRITICAL] Saknar musikmapp: {config.FILE_DIRECTORY}")
        sys.exit(1)
        
    success = generate_master_list()
    if success:
        print("--- Listan uppdaterades utan problem! ---")
        sys.exit(0)
    else:
        print("--- FEL: Kunde inte generera listan. ---")
        sys.exit(1)
