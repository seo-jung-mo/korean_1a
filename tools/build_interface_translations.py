import ast
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
SOURCE = ROOT / "app.py"
OUTPUT = ROOT / "assets" / "interface_translations.json"
LANGUAGES = {
    "日本語": "ja",
    "中文": "zh-CN",
    "Tiếng Việt": "vi",
    "नेपाली": "ne",
    "Монгол": "mn",
    "ไทย": "th",
    "O‘zbekcha": "uz",
    "မြန်မာ": "my",
    "বাংলা": "bn",
}


def extract_english_phrases():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    phrases = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "interface_text" or len(node.args) < 2:
            continue
        english = node.args[1]
        if isinstance(english, ast.Constant) and isinstance(english.value, str):
            phrases.add(english.value)
    return sorted(phrases)


def protect(text):
    saved = []
    pattern = re.compile(
        r":[a-z]+\[.*?\]|\{[^{}]+\}|[가-힣ㄱ-ㅎㅏ-ㅣ]+|[-–—]?\([으]?\)[가-힣?]+|`[^`]+`|\*\*.*?\*\*"
    )

    def replace(match):
        saved.append(match.group(0))
        return f" ZXQ{len(saved) - 1}QXZ "

    return pattern.sub(replace, text), saved


def restore(text, saved):
    for index, value in enumerate(saved):
        token = f"ZXQ{index}QXZ"
        text = re.sub(rf"\s*{token}\s*", value, text, flags=re.IGNORECASE)
    return html.unescape(text).strip()


def translate(text, language_code):
    protected, saved = protect(text)
    query = urllib.parse.urlencode(
        {"sl": "en", "tl": language_code, "q": protected}
    )
    url = f"https://translate.google.com/m?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                page = response.read().decode("utf-8")
            result = re.search(r'<div class="result-container">(.*?)</div>', page, re.DOTALL)
            if not result:
                raise RuntimeError("Translation result was not found")
            translated = re.sub(r"<[^>]+>", "", result.group(1))
            return restore(translated, saved)
        except Exception:
            if attempt == 7:
                raise
            time.sleep(min(30, 2 ** attempt))


def main():
    phrases = extract_english_phrases()
    existing = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    for language, code in LANGUAGES.items():
        table = existing.setdefault(language, {})
        missing = [phrase for phrase in phrases if phrase not in table]
        print(f"{language}: {len(missing)} missing")
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(translate, phrase, code): phrase for phrase in missing}
            for index, future in enumerate(as_completed(futures), 1):
                phrase = futures[future]
                table[phrase] = future.result()
                if index % 50 == 0:
                    OUTPUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"  {index}/{len(missing)}", flush=True)
        OUTPUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
