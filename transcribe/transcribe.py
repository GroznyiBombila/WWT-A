"""Транскрипция записей онлайн-Q&A (mp3/mp4/m4a/...) в markdown с таймкодами.

    python transcribe/transcribe.py media/qa-2026-10-25.mp3 [ещё файлы...]
    python transcribe/transcribe.py media/                  # все файлы папки
    python transcribe/transcribe.py media/ --out docs/qa/transcripts

Медиа в git не кладём (media/ в .gitignore), в репо идёт только транскрипт
с таймкодами. Модель faster-whisper large-v3-turbo: на GPU 4 ГБ влезает в
float16; без CUDA сама переключается на CPU int8 (медленнее в разы).
Готовый транскрипт повторно не пересчитывается — удали .md, чтобы переснять.
"""
import argparse
import os
import sys
import time


def _add_cuda_dlls():
    """cuBLAS/cuDNN из pip-колёс nvidia-* (Windows не ищет их в site-packages сам)."""
    import site
    for sp in site.getsitepackages():
        for lib in ("cublas", "cudnn"):
            d = os.path.join(sp, "nvidia", lib, "bin")
            if os.path.isdir(d):
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


if os.name == "nt":
    _add_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(_REPO, "qa", "transcripts")
MEDIA_EXT = {".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".webm", ".mkv", ".mov"}


def _ts(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _load_model(name):
    try:
        model = WhisperModel(name, device="cuda", compute_type="float16")
        # библиотеки CUDA грузятся лениво, на первом encode: проверяем сразу,
        # иначе падение случится посреди первого файла
        import numpy as np
        list(model.transcribe(np.zeros(16000, dtype=np.float32), language="ru")[0])
        return model, "cuda"
    except Exception as e:  # нет CUDA / нет cuBLAS / не хватило памяти
        print(f"CUDA недоступна ({e.__class__.__name__}: {e}), считаю на CPU")
        return WhisperModel(name, device="cpu", compute_type="int8"), "cpu"


def _inputs(paths):
    for p in paths:
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                if os.path.splitext(f)[1].lower() in MEDIA_EXT:
                    yield os.path.join(p, f)
        else:
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--out", default=DEFAULT_OUT, help="куда класть транскрипты")
    args = ap.parse_args()

    files = list(_inputs(args.paths))
    if not files:
        sys.exit("Нет медиафайлов")
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    model, device = None, None
    for path in files:
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(out_dir, f"{base}.md")
        if os.path.exists(out):
            print(f"пропуск (уже есть): {out}")
            continue
        if model is None:
            model, device = _load_model(args.model)
        t0 = time.time()
        segments, info = model.transcribe(path, language=args.lang, vad_filter=True,
                                          beam_size=5, condition_on_previous_text=False)
        lines = [f"[{_ts(s.start)}] {s.text.strip()}" for s in segments]
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"# Транскрипт: {os.path.basename(path)}\n\n"
                    f"Длительность {_ts(info.duration)} · модель {args.model} ({device}) · "
                    f"автоматическая расшифровка, имена и термины сверять по записи.\n\n")
            f.write("\n\n".join(lines) + "\n")
        print(f"{out}: {len(lines)} сегментов за {time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
