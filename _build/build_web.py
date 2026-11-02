"""Assemble the static site coach-card-checker-web/ from the two desktop apps.
Re-run after any change to the apps: python build_web.py <out_dir>"""
import json, os, re, shutil, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/site"
APPS = {"2026": "/home/claude/w2026", "2027": "/home/claude/CC reviews/coach-card-checker-2027"}
SHARED_SRC = "/home/claude/web/shared"
MODELS = "/tmp/claude-0/-home-claude/1663c371-0208-5db8-837d-8be2bf074fde/scratchpad/ocr/rapidocr_onnxruntime/models/"
SKILL_FILES = ["appendix3_requirements.json", "catalogue_hybrid.json", "catalogue_pair_acro.json",
               "catalogue_team_acro.json", "compatibility_grids.json", "reserved_codes.json",
               "prefixed_codes_2027.json"]
SCRIPTS = ["check_coach_card.py", "code_validator.py", "acro_parser.py", "acro_dd.py"]

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

# ---- shared: runtime, OCR, bridge, models ----
sh = os.path.join(OUT, "shared")
os.makedirs(os.path.join(sh, "models"))
for f in ("runtime.js", "ocr.js", "web_bridge.py"):
    shutil.copy(os.path.join(SHARED_SRC, f), sh)
shutil.copy(MODELS + "ch_PP-OCRv4_det_infer.onnx", os.path.join(sh, "models", "det.onnx"))
shutil.copy(MODELS + "ch_PP-OCRv4_rec_infer.onnx", os.path.join(sh, "models", "rec.onnx"))
import onnxruntime as ort
keys = ort.InferenceSession(MODELS + "ch_PP-OCRv4_rec_infer.onnx").get_modelmeta().custom_metadata_map["character"]
open(os.path.join(sh, "models", "keys.txt"), "w", encoding="utf-8").write(keys if keys.endswith("\n") else keys + "\n")

# ---- one folder per rules year ----
for year, app in APPS.items():
    d = os.path.join(OUT, year)
    os.makedirs(os.path.join(d, "py", "skill", "scripts"))
    os.makedirs(os.path.join(d, "py", "webapp"))
    files = []
    for f in SKILL_FILES:
        p = os.path.join(app, "skill", f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(d, "py", "skill", f)); files.append("skill/" + f)
    for f in SCRIPTS:
        shutil.copy(os.path.join(app, "skill", "scripts", f), os.path.join(d, "py", "skill", "scripts", f))
        files.append("skill/scripts/" + f)
    shutil.copy(os.path.join(app, "webapp", "extract.py"), os.path.join(d, "py", "webapp", "extract.py"))
    files.append("webapp/extract.py")
    json.dump({"year": year, "files": files}, open(os.path.join(d, "py", "manifest.json"), "w"), indent=1)

    st = os.path.join(app, "webapp", "static")
    shutil.copy(os.path.join(st, "app.js"), d)
    shutil.copy(os.path.join(st, "style.css"), d)
    html = open(os.path.join(st, "index.html"), encoding="utf-8").read()
    html = html.replace('href="/static/style.css"', 'href="style.css"')
    html = html.replace('<script src="/static/app.js"></script>',
                        '<script src="../shared/ocr.js"></script>\n'
                        f'  <script src="../shared/runtime.js" data-year="{year}"></script>\n'
                        '  <script src="app.js"></script>')
    html = html.replace("<span>Runs locally · no data leaves this machine</span>",
                        "<span>Runs in your browser · your coach cards never leave this device</span>"
                        f' · <a href="../">Change rules year</a>')
    assert 'src="app.js"' in html and "../shared/runtime.js" in html, year
    open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(html)

# ---- landing page + extras ----
shutil.copy("/home/claude/web/index.html", OUT)
shutil.copy("/home/claude/web/serve.bat", OUT)
shutil.copy("/home/claude/web/README.md", OUT)
open(os.path.join(OUT, ".nojekyll"), "w").close()      # GitHub Pages: serve files as-is
print("site built in", OUT)
for root, _, fs in os.walk(OUT):
    for f in fs:
        p = os.path.join(root, f)
        print(f"{os.path.getsize(p):>10}  {os.path.relpath(p, OUT)}")
