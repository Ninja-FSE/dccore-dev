# update_list.py - Renodlad OmenServe-layout (Enbart filnamn på raderna)
import os
import sys
import datetime
import zipfile
import time
import config

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
    """Skannar musikmappen, rensar gamla filer först och bygger listan med mapprobrik-layout"""
    if not os.path.exists(config.LOCAL_LIST_DIR):
        os.makedirs(config.LOCAL_LIST_DIR)

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    txt_filename = f"{config.LIST_BASE_NAME}-{today}.txt"
    zip_filename = f"{config.LIST_BASE_NAME}-{today}.zip"
    
    txt_path = os.path.join(config.LOCAL_LIST_DIR, txt_filename)
    zip_path = os.path.join(config.LOCAL_LIST_DIR, zip_filename)
    
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
        for file in files:
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

    all_files_data.sort(key=lambda x: (x[0].lower(), x[1].lower()))

    total_files_count = len(all_files_data)
    scan_end = time.time()
    elapsed_seconds = scan_end - scan_start
    if elapsed_seconds <= 0:
        elapsed_seconds = 0.1

    files_per_second = int(total_files_count / elapsed_seconds)
    formatted_size = format_total_size(total_bytes)
    
    time_struct = time.gmtime(elapsed_seconds)
    duration_str = time.strftime("%H:%M:%S", time_struct)

    day = datetime.datetime.now().day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    date_header_str = datetime.datetime.now().strftime(f"{day}{suffix} %b %Y")

    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"List of {total_files_count:,} Files ({formatted_size}) generated on {date_header_str} in {duration_str} ( {files_per_second:,} Files Per Second )\n\n")
            f.write(f"To request a file, copy/paste to the channel... !{config.NICKNAME} FILENAME eg. !{config.NICKNAME} Songname.flac\n\n\n")

            current_folder = None
            
            for folder, filename, bytes_size in all_files_data:
                if folder != current_folder:
                    current_folder = folder
                    display_folder = f"D:\\MUSIC\\{folder}\\" if folder else "D:\\MUSIC\\"
                    f.write(f"\n=====================================================\n")
                    f.write(f"{display_folder}\n")
                    f.write(f"=====================================================\n")
                
                single_file_size = format_size_human(bytes_size)
                
                # GENOMBROTT: Vi skriver BARA ut det rena filnamnet på raden! Inga undermappar här!
                f.write(f"!{config.NICKNAME} {filename}  ::INFO:: {single_file_size}\n")
                    
        print(f"[LIST-GEN] Textlista skapad utan problem: {txt_path}")
        
        with open(SIZE_FILE_PATH, "w", encoding="utf-8") as sf:
            sf.write(formatted_size)
            
        with open(RAWBYTES_FILE_PATH, "w", encoding="utf-8") as rbf:
            rbf.write(str(total_bytes))
            
        if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
            print(f"[LIST-GEN] Packar ner listan i zip-arkiv...")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(txt_path, arcname=txt_filename)
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
