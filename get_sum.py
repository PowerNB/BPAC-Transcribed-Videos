#!/usr/bin/env python3
"""
Скрипт для генерации конспектов из транскрипций видео обучающего курса.
"""

import os
import re
import sys
import subprocess
import glob
from pathlib import Path


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

OUTPUT_DIR = "output"
CLAUDE_CMD = r"C:\Users\Proger4\AppData\Roaming\npm\claude.cmd"
SUMMARY_OUTPUT_DIR = "claude-summary"
SKIP_EXISTING = True
VERBOSE = True
TIMEOUT = 600


# ─────────────────────────────────────────────
# ПРОМПТ ДЛЯ CLAUDE
# ─────────────────────────────────────────────

SUMMARY_PROMPT_TEMPLATE = """Ты - эксперт по составлению учебных конспектов. 
Тебе нужно создать МАКСИМАЛЬНО ПОДРОБНЫЙ и ИНФОРМАТИВНЫЙ конспект видеоурока по Bitrix24 "Автоматизация бизнес-процессов".

КОНТЕКСТ УРОКА (из описания модуля):
{module_context}

ТРАНСКРИПЦИЯ ВИДЕОУРОКА:
{transcription}

{links_section}

ЗАДАЧА:
Создай подробный конспект этого урока в формате Markdown. Конспект будет использоваться как ИНСТРУКЦИЯ для будущей работы, поэтому:

1. НЕ ТЕРЯЙ суть - включи все важные концепции, термины, определения
2. Структурируй информацию по разделам с заголовками
3. Выдели ключевые моменты - что нужно запомнить и применять на практике
4. Включи конкретные шаги - если описываются действия в интерфейсе Bitrix24, опиши их пошагово
5. Добавь примеры из транскрипции - они помогают понять контекст
6. Если есть ссылки - включи ключевую информацию из них в соответствующий раздел
7. В конце добавь раздел "Главное" - 5-10 ключевых выводов урока

Формат конспекта:
# [Название урока]

## О чём этот урок
[2-3 предложения о чём урок и зачем он нужен]

## Основные понятия
[Все термины и определения из урока]

## [Тематические разделы по содержанию урока]
[Подробное содержание каждого раздела]

## Пошаговые инструкции (если есть)
[Конкретные шаги для выполнения действий в Bitrix24]

## Главное
[Ключевые выводы и то, что нужно помнить]

Пиши конспект на русском языке. Будь подробным - лучше написать больше, чем потерять важную информацию.

ВАЖНО: Начни ответ сразу с символа # (заголовок конспекта). Не пиши никаких вступлений, пояснений или описаний своих действий до и после конспекта."""


# ─────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────

def log(msg):
    if VERBOSE:
        print(msg, flush=True)


def find_video_files(output_dir: str) -> list:
    video_files = []
    pattern = os.path.join(output_dir, "**", "video-*.md")
    for filepath in sorted(glob.glob(pattern, recursive=True)):
        path = Path(filepath)
        folder = path.parent
        filename = path.stem
        match = re.match(r'video-(\d+)-(\d+)-(.*)', filename)
        if not match:
            continue
        module_num = match.group(1)
        video_num = match.group(2)
        video_name = match.group(3)
        module_file = folder / f"module-{module_num}.md"
        summary_name = f"summary-{module_num}-{video_num}-{video_name}.md"
        relative_subfolder = path.parent.relative_to(output_dir)
        summary_path = Path(SUMMARY_OUTPUT_DIR) / relative_subfolder / summary_name
        video_files.append({
            "transcription_path": filepath,
            "module_file": str(module_file) if module_file.exists() else None,
            "summary_path": str(summary_path),
            "module_num": module_num,
            "video_num": video_num,
            "video_name": video_name,
        })
    return video_files


def read_file(filepath: str) -> str:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        log(f"  Не удалось прочитать {filepath}: {e}")
        return ""


def extract_links(text: str) -> list:
    md_links = re.findall(r'\[.*?\]\((https?://[^\)]+)\)', text)
    plain_links = re.findall(r'(?<!\()(https?://[^\s\)\"\']+)', text)
    return list(dict.fromkeys(md_links + plain_links))


def fetch_url_content(url: str) -> str:
    try:
        result = subprocess.run(
            f'curl -s -L --max-time 15 -A "Mozilla/5.0" "{url}"',
            capture_output=True, text=True, timeout=20, shell=True
        )
        if result.returncode != 0 or not result.stdout:
            return ""
        content = result.stdout
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'&nbsp;', ' ', content)
        content = re.sub(r'&[a-zA-Z]+;', '', content)
        content = re.sub(r'\s+', ' ', content).strip()
        return content[:3000] if len(content) > 3000 else content
    except Exception as e:
        log(f"    Ошибка при загрузке {url}: {e}")
        return ""


def build_prompt(video_info: dict, transcription: str, module_context: str) -> str:
    links = extract_links(transcription)
    links_section = ""
    if links:
        log(f"    Найдено ссылок: {len(links)}, загружаю...")
        parts = []
        for url in links[:5]:
            log(f"       -> {url}")
            content = fetch_url_content(url)
            if content:
                parts.append(f"URL: {url}\nСодержимое:\n{content}\n")
        if parts:
            links_section = "СОДЕРЖИМОЕ ССЫЛОК ИЗ УРОКА:\n" + "\n---\n".join(parts)
    return SUMMARY_PROMPT_TEMPLATE.format(
        module_context=module_context or "Нет описания модуля",
        transcription=transcription,
        links_section=links_section,
    )


SYSTEM_PROMPT = (
    "Ты - эксперт по составлению учебных конспектов. "
    "Твоя задача - выводить ТОЛЬКО готовый конспект в формате Markdown, без каких-либо пояснений, "
    "вступлений, описаний того что ты сделал, и без фраз вроде 'Конспект создан', 'Что вошло в конспект' и т.п. "
    "Просто выведи сам конспект - от первого заголовка до последней строки - и больше ничего."
)


def run_claude(prompt: str) -> str:
    prompt_bytes = prompt.encode('utf-8')

    proc = subprocess.Popen(
        [
            CLAUDE_CMD,
            "--print",
            "--input-format", "text",
            "--dangerously-skip-permissions",
            "--system-prompt", SYSTEM_PROMPT,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = proc.communicate(input=prompt_bytes, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"Превышено время ожидания {TIMEOUT} сек.")

    stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
    stderr = stderr_bytes.decode('utf-8', errors='replace').strip()

    if stderr:
        log(f"         stderr: {stderr[:300]}")

    if proc.returncode != 0 or not stdout:
        raise RuntimeError(
            f"Claude CLI завершился с кодом {proc.returncode}. "
            f"stderr: {stderr[:400] if stderr else '(пусто)'}"
        )

    return stdout


def save_summary(path: str, content: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


# ─────────────────────────────────────────────
# ОСНОВНАЯ ЛОГИКА
# ─────────────────────────────────────────────

def process_video(video_info: dict, idx: int, total: int) -> bool:
    transcription_path = video_info["transcription_path"]
    summary_path = video_info["summary_path"]
    module_num = video_info["module_num"]
    video_num = video_info["video_num"]
    video_name = video_info["video_name"]

    log(f"\n[{idx}/{total}] Модуль {module_num}, Видео {video_num}: {video_name}")
    log(f"         Транскрипция: {transcription_path}")
    log(f"         Конспект:     {summary_path}")

    if SKIP_EXISTING and os.path.exists(summary_path):
        log("         [пропуск] Конспект уже существует.")
        return True

    transcription = read_file(transcription_path)
    if not transcription:
        log("         [ошибка] Не удалось прочитать транскрипцию.")
        return False

    module_context = ""
    if video_info["module_file"]:
        module_context = read_file(video_info["module_file"])
        log(f"         Описание модуля загружено: {video_info['module_file']}")

    log("         Формирую промпт...")
    prompt = build_prompt(video_info, transcription, module_context)
    log(f"         Размер промпта: {len(prompt)} символов")

    log("         Запускаю Claude CLI...")
    try:
        summary = run_claude(prompt)
    except RuntimeError as e:
        log(f"         [ошибка] {e}")
        return False

    save_summary(summary_path, summary)
    log(f"         Готово! Сохранено {len(summary)} символов")
    return True


def main():
    if len(sys.argv) > 1:
        output_dir = sys.argv[1]
    else:
        output_dir = OUTPUT_DIR

    if not os.path.isdir(output_dir):
        print(f"Директория не найдена: {output_dir}")
        sys.exit(1)

    print(f"Генерация конспектов из папки: {output_dir}")
    print(f"Конспекты сохраняются в:       {SUMMARY_OUTPUT_DIR}/")
    print(f"Claude CLI:                    {CLAUDE_CMD}")
    print(f"Пропускать существующие:       {SKIP_EXISTING}")
    print(f"Таймаут:                       {TIMEOUT} сек", flush=True)

    video_files = find_video_files(output_dir)
    if not video_files:
        print("Видео-файлы не найдены.")
        sys.exit(1)

    print(f"\nНайдено видео-файлов: {len(video_files)}", flush=True)

    success_count = 0
    fail_count = 0

    for idx, video_info in enumerate(video_files, 1):
        try:
            ok = process_video(video_info, idx, len(video_files))
            if ok:
                success_count += 1
            else:
                fail_count += 1
        except KeyboardInterrupt:
            print("\nПрервано пользователем.")
            break
        except Exception as e:
            log(f"         [ошибка] Неожиданная ошибка: {e}")
            fail_count += 1

    print(f"\n{'='*50}")
    print(f"Успешно: {success_count}")
    print(f"Ошибок:  {fail_count}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()