import subprocess
import re
import sys
import os
import glob


def download_subtitles(url: str, lang: str = "ru") -> str | None:
    print(f"Скачиваю субтитры для: {url}")
    for flags in [["--write-sub"], ["--write-auto-sub"]]:
        subprocess.run([
            "yt-dlp", *flags,
            "--sub-langs", lang,
            "--skip-download",
            "--convert-subs", "srt",
            "-o", "%(title)s",
            url
        ], capture_output=True, text=True)

        srt_files = glob.glob("*.srt")
        if srt_files:
            print(f"Найден файл субтитров: {srt_files[0]}")
            return srt_files[0]

    print("Субтитры не найдены.")
    return None


def merge_sliding_subtitles(lines: list[str]) -> str:
    """
    Склеивает субтитры со скользящим окном в единый текст.
    Каждый новый блок — продолжение предыдущего, они частично перекрываются.
    """
    if not lines:
        return ""

    result = lines[0]

    for current in lines[1:]:
        # Ищем максимальное перекрытие: конец result совпадает с началом current
        max_overlap = min(len(result), len(current))
        overlap_len = 0
        for i in range(max_overlap, 0, -1):
            if result.endswith(current[:i]):
                overlap_len = i
                break

        if overlap_len > 0:
            result += current[overlap_len:]
        else:
            # Нет перекрытия — новое предложение, добавляем с пробелом
            result += " " + current

    return result


def srt_to_md(srt_path: str, md_path: str | None = None) -> str:
    if md_path is None:
        md_path = os.path.splitext(srt_path)[0] + ".md"

    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r'\n\n+', content.strip())

    lines = []
    for block in blocks:
        parts = block.strip().split('\n')
        if len(parts) < 3:
            continue
        text = ' '.join(parts[2:]).strip()
        text = re.sub(r'<[^>]+>', '', text)  # убираем HTML-теги
        text = re.sub(r'\s+', ' ', text)     # нормализуем пробелы
        if text:
            lines.append(text)

    # Склеиваем скользящее окно в единый текст
    merged = merge_sliding_subtitles(lines)

    # Разбиваем на абзацы по знакам конца предложения
    merged = re.sub(r'([.!?])\s+', r'\1\n\n', merged)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(merged.strip())

    print(f"Сохранено в: {md_path}")
    return md_path


def main():
    if len(sys.argv) < 2:
        print("Использование: python script.py <URL> [язык]")
        print("Пример: python script.py https://vkvideo.ru/video-211967493_456239178 ru")
        sys.exit(1)

    url = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else "ru"

    srt_path = download_subtitles(url, lang)
    if srt_path:
        md_path = srt_to_md(srt_path)
        print(f"\nГотово! Markdown файл: {md_path}")
    else:
        print("Не удалось получить субтитры.")
        sys.exit(1)


if __name__ == "__main__":
    main()