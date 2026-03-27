import subprocess
import re
import sys
import glob
import shutil
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import whisper
# ... остальной код


# ── настройки ──────────────────────────────────────────────────────────────
WHISPER_MODEL = "large"   # tiny / base / small / medium / large
LANG          = "ru"
OUTPUT_DIR    = "./output"
URLS_FILE     = "urls.txt"
# ───────────────────────────────────────────────────────────────────────────


def sanitize(name: str) -> str:
    name = name.strip().strip('\u200b\u200c\u200d\ufeff\xa0')
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_urls_file(filepath: str) -> list[tuple[str, str]]:
    entries = []
    current_module = "Без модуля"
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip().strip('\u200b\u200c\u200d\ufeff\xa0')
            if not line or line.startswith("#"):
                continue
            if re.match(r'https?://', line):
                entries.append((current_module, line))
            else:
                current_module = line
    return entries


def get_video_title(url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", "--get-title", "--no-playlist", url],
        capture_output=True, text=True
    )
    title = result.stdout.strip().split('\n')[0]
    return sanitize(title) or "video"


def download_audio(url: str, out_path: str) -> str | None:
    """Скачивает аудио в mp3, возвращает путь к файлу."""
    result = subprocess.run([
        "yt-dlp",
        "--no-playlist",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "0",
        "-o", out_path,
        url
    ], capture_output=True, text=True)

    if os.path.isfile(out_path):
        return out_path

    # yt-dlp иногда добавляет расширение сам
    candidates = glob.glob(out_path.replace(".mp3", "*"))
    if candidates:
        return candidates[0]

    print(f"  [ОШИБКА] Не удалось скачать аудио:\n{result.stderr[-500:]}")
    return None


def transcribe_with_whisper(audio_path: str, model, lang: str) -> str:
    print(f"  Транскрибирую через Whisper ({WHISPER_MODEL})...")
    result = model.transcribe(audio_path, language=lang, verbose=False)
    return result["text"].strip()


def text_to_md(text: str) -> str:
    """Разбивает сплошной текст на абзацы по знакам конца предложения."""
    text = re.sub(r'([.!?])\s+', r'\1\n\n', text)
    return text.strip()


def process_url(url: str, module_dir: str, index: int, total: int, model, lang: str):
    print(f"\n  [{index}/{total}] {url}")

    tmp_dir = os.path.join(module_dir, f"_tmp_{index}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        title = get_video_title(url)
        print(f"  Название: {title}")

        md_path = os.path.join(module_dir, f"{title}.md")

        # Пропускаем если уже транскрибировано
        if os.path.isfile(md_path):
            print(f"  [ПРОПУЩЕНО] Уже существует: {md_path}")
            return

        audio_out = os.path.join(tmp_dir, "audio.mp3")
        audio_path = download_audio(url, audio_out)

        if not audio_path:
            log_skipped(module_dir, url, title)
            return

        text = transcribe_with_whisper(audio_path, model, lang)
        md_content = text_to_md(text)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{md_content}")

        print(f"  [OK] → {md_path}")

    except Exception as e:
        print(f"  [ОШИБКА] {e}")
        log_skipped(module_dir, url, "ошибка")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def log_skipped(module_dir: str, url: str, reason: str):
    log_path = os.path.join(os.path.dirname(module_dir), "skipped.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{reason}\t{url}\n")
    print(f"  [ПРОПУЩЕНО] Записано в skipped.txt")


def main():
    urls_file  = sys.argv[1] if len(sys.argv) > 1 else URLS_FILE
    lang       = sys.argv[2] if len(sys.argv) > 2 else LANG
    output_dir = sys.argv[3] if len(sys.argv) > 3 else OUTPUT_DIR

    entries = parse_urls_file(urls_file)
    if not entries:
        print("Нет URL для обработки.")
        sys.exit(1)

    # Группируем по модулям
    modules: dict[str, list[str]] = {}
    for module_name, url in entries:
        modules.setdefault(module_name, []).append(url)

    total = len(entries)
    print(f"Загружаю модель Whisper '{WHISPER_MODEL}'...")
    model = whisper.load_model(WHISPER_MODEL)
    print(f"Модель загружена.\n")
    print(f"Модулей: {len(modules)} | Видео: {total} | Язык: {lang}")
    print(f"Папка вывода: {output_dir}\n")

    global_index = 0
    for module_name, urls in modules.items():
        folder_name = sanitize(module_name)
        module_dir  = os.path.join(output_dir, folder_name)
        os.makedirs(module_dir, exist_ok=True)

        print(f"\n{'='*55}")
        print(f"  {module_name}  ({len(urls)} видео)")
        print(f"{'='*55}")

        for url in urls:
            global_index += 1
            process_url(url, module_dir, global_index, total, model, lang)

    print(f"\n✅ Готово! Файлы: {output_dir}")
    skipped = os.path.join(output_dir, "skipped.txt")
    if os.path.isfile(skipped):
        print(f"⚠️  Пропущенные видео записаны в: {skipped}")


if __name__ == "__main__":
    main()