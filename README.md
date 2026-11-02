# Coach Card Checker — version web (sans serveur)

Site statique : tout tourne **dans le navigateur** de la personne (règles en Python via Pyodide,
lecture des photos via onnxruntime-web + opencv.js, mêmes modèles OCR que l'app de bureau).
Aucune carte n'est envoyée nulle part. Hébergement gratuit (GitHub Pages ou Cloudflare Pages).

## Tester sur ton ordi
Double-clic sur `serve.bat` → le navigateur s'ouvre sur http://127.0.0.1:8090/
(Il faut passer par ce petit serveur : ouvrir `index.html` directement ne marche pas.)

## Structure
- `index.html` — page d'accueil : choix Règles 2026 / Règles 2027
- `2026/`, `2027/` — l'interface de chaque année + `py/` (le moteur de règles, copie exacte des apps de bureau)
- `shared/runtime.js` — remplace le serveur (répond à /api/routines, /api/check, /api/extract dans le navigateur)
- `shared/ocr.js` — lecture des photos (copie fidèle de RapidOCR)
- `shared/web_bridge.py` — fait le lien entre la lecture des photos et le code Python existant
- `shared/models/` — modèles OCR PP-OCRv4 (détection 4,7 Mo, reconnaissance 10,9 Mo)

## Mettre à jour après un changement de règles
Les fichiers de `2026/py` et `2027/py` sont des copies des apps de bureau : on les régénère avec
`build_web.py` (Claude s'en charge).

## Validation (octobre 2026)
Version de référence de la lecture photo (même algorithme que `ocr.js`) sur les 47 cartes 2026 d'exemple :
**222 DD d'acros sur 226 identiques au DD calculé, 0 fausse alerte** (app de bureau : 217).
