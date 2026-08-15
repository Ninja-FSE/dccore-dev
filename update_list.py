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

    print(f"[LIST-CLEAN] Rensar gamla listor från {config.LOCAL_LIST_DIR}...")
    removed_count = 0
    for item in os.listdir(config.LOCAL_LIST_DIR):
        if item.startswith(config.LIST_BASE_NAME) and (item.endswith(".txt") or item.endswith(".zip")):
            try:
                os.remove(os.path.join(config.LOCAL_LIST_DIR, item))
                removed_count += 1
            except Exception as e:
                print(f"[LIST-CLEAN ERROR] Kunde inte ta bort {item}: {e}")
    if removed_count > 0:
        print(f"[LIST-CLEAN] Tog bort {removed_count} gamla filer permanent.")

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
        with open(txt_path, "w", encoding="utf-8") as f, \
             open(rar_path, "w", encoding="utf-8") as f_rar:
                 
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

        print(f"[LIST-GEN] Textlista skapad utan problem: {txt_path}")
        print(f"[LIST-GEN] RAR-albumlista skapad utan problem: {rar_path}")
        
        with open(SIZE_FILE_PATH, "w", encoding="utf-8") as sf:
            sf.write(formatted_size)
            
        with open(RAWBYTES_FILE_PATH, "w", encoding="utf-8") as rbf:
            rbf.write(str(total_bytes))
            
        if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
            print(f"[LIST-GEN] Packar ner BÅDA textlistorna i master-zip-arkiv...")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(txt_path, arcname=os.path.basename(txt_path))
                if os.path.exists(rar_path) and os.path.getsize(rar_path) > 0:
                    zipf.write(rar_path, arcname=os.path.basename(rar_path))
            print(f"[LIST-GEN] Zip-arkiv skapat utan fel: {zip_path}")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"[LIST-GEN ERROR] Misslyckades att generera listor: {e}")
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
