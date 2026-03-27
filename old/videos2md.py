import subprocess
import re
import sys
import os
import glob
import shutil


def download_subtitles(url: str, lang: str, output_dir: str) -> str | None:
    for flags in [["--write-sub"], ["--write-auto-sub"]]:
        subprocess.run([
            "yt-dlp", *flags,
            "--sub-langs", lang,
            "--skip-download",
            "--convert-subs", "srt",
            "-o", os.path.join(output_dir, "%(title)s"),
            url
        ], capture_output=True, text=True)

        srt_files = glob.glob(os.path.join(output_dir, "*.srt"))
        if srt_files:
            return srt_files[0]

    return None


def merge_sliding_subtitles(lines: list[str]) -> str:
    if not lines:
        return ""

    result = lines[0]

    for current in lines[1:]:
        max_overlap = min(len(result), len(current))
        overlap_len = 0
        for i in range(max_overlap, 0, -1):
            if result.endswith(current[:i]):
                overlap_len = i
                break

        if overlap_len > 0:
            result += current[overlap_len:]
        else:
            result += " " + current

    return result


def srt_to_md(srt_path: str, md_path: str) -> str:
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r'\n\n+', content.strip())

    lines = []
    for block in blocks:
        parts = block.strip().split('\n')
        if len(parts) < 3:
            continue
        text = ' '.join(parts[2:]).strip()
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text)
        if text:
            lines.append(text)

    merged = merge_sliding_subtitles(lines)
    merged = re.sub(r'([.!?])\s+', r'\1\n\n', merged)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(merged.strip())

    return md_path


def get_video_title(url: str) -> str:
    result = subprocess.run([
        "yt-dlp", "--get-title", "--no-playlist", url
    ], capture_output=True, text=True)
    title = result.stdout.strip()
    title = re.sub(r'[\\/*?:"<>|]', '_', title)
    return title or "video"


def sanitize_folder_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    return name


def parse_urls_file(filepath: str) -> list[tuple[str, str]]:
    """
    Парсит файл со структурой:
        Модуль 1
        https://...
        https://...

        Модуль 2
        https://...
    """
    entries = []
    current_module = "Без модуля"

    with open(filepath, "r", encoding="utf-8-sig") as f:  # utf-8-sig убирает BOM
        for line in f:
            # Убираем все виды пробелов и невидимых символов
            line = line.strip().strip('\u200b\u200c\u200d\ufeff\xa0')

            if not line or line.startswith("#"):
                continue

            # Проверяем URL более надёжно
            if re.match(r'https?://', line):
                entries.append((current_module, line))
            else:
                current_module = line


    return entries


def process_url(url: str, lang: str, module_dir: str, index: int, total: int):
    print(f"\n  [{index}/{total}] {url}")

    tmp_dir = os.path.join(module_dir, f"_tmp_{index}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        title = get_video_title(url)
        print(f"  Название: {title}")

        srt_path = download_subtitles(url, lang, tmp_dir)

        if not srt_path:
            print(f"  [ПРОПУЩЕНО] Субтитры не найдены")
            return

        md_path = os.path.join(module_dir, f"{title}.md")
        srt_to_md(srt_path, md_path)
        print(f"  [OK] → {md_path}")

    except Exception as e:
        print(f"  [ОШИБКА] {e}")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    if len(sys.argv) < 2:
        print("Использование: python script.py urls.txt [--lang ru] [--out ./output]")
        sys.exit(1)

    args = sys.argv[1:]
    lang = "ru"
    output_dir = "./output"
    urls_file = None

    i = 0
    while i < len(args):
        if args[i] == "--lang" and i + 1 < len(args):
            lang = args[i + 1]
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            output_dir = args[i + 1]
            i += 2
        else:
            urls_file = args[i]
            i += 1

    if not urls_file or not os.path.isfile(urls_file):
        print(f"Файл не найден: {urls_file}")
        sys.exit(1)

    entries = parse_urls_file(urls_file)

    if not entries:
        print("Нет URL для обработки.")
        sys.exit(1)

    # Группируем по модулям
    modules: dict[str, list[str]] = {}
    for module_name, url in entries:
        modules.setdefault(module_name, []).append(url)

    total_videos = len(entries)
    print(f"Модулей: {len(modules)}, Видео всего: {total_videos}")
    print(f"Язык: {lang} | Папка вывода: {output_dir}\n")

    global_index = 0
    for module_name, urls in modules.items():
        folder_name = sanitize_folder_name(module_name)
        module_dir = os.path.join(output_dir, folder_name)
        os.makedirs(module_dir, exist_ok=True)

        print(f"\n{'='*50}")
        print(f"📁 {module_name} ({len(urls)} видео) → {module_dir}")
        print(f"{'='*50}")

        for j, url in enumerate(urls, start=1):
            global_index += 1
            process_url(url, lang, module_dir, global_index, total_videos)

    print(f"\n✅ Готово! Все файлы сохранены в: {output_dir}")


if __name__ == "__main__":
    main()