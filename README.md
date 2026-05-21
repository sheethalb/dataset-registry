# PR Lab — Dataset Registry

A self-updating web dashboard for the shared dataset folder at `/home/janus/iwi5-datasets/`.

**Live dashboard →** https://sheethalb.github.io/dataset-registry/

---

## How it works

```
/home/janus/iwi5-datasets/   ← shared datasets on the HPC
└── .registry/
    └── datasets.json        ← generated cache (single source of truth)

GitHub repo (this repo)
├── data/datasets.json       ← copy pushed here for GitHub Pages
└── web/index.html           ← dashboard served at the URL above
```

1. `scanner.py` walks the datasets folder, auto-detects fields, calls Papers with Code + HuggingFace APIs, and writes `.registry/datasets.json`
2. `sync.sh` copies that JSON to `data/` and pushes to GitHub
3. GitHub Pages serves `web/index.html` which fetches `data/datasets.json`

---

## Quick start (first-time GitHub setup)

### Step 1 — Create the GitHub repository

```bash
# On your local machine or via github.com:
# Create a new EMPTY repo at: https://github.com/sheethalb/dataset-registry
# (no README, no .gitignore)
```

### Step 2 — Push this folder to GitHub

```bash
cd /home/woody/iwi5/iwi5145h/Databases/dataset-registry

git init
git add .
git commit -m "feat: initial dataset registry"
git branch -M main
git remote add origin https://github.com/sheethalb/dataset-registry.git
git push -u origin main
```

### Step 3 — Enable GitHub Pages

1. Go to https://github.com/sheethalb/dataset-registry/settings/pages
2. Source: **Deploy from a branch**
3. Branch: `main`  |  Folder: `/web`
4. Click **Save**

Your dashboard will be live at **https://sheethalb.github.io/dataset-registry/** within ~2 minutes.

### Step 4 — Run the scanner for the first time

```bash
# Activate the mri conda env
source /home/woody/iwi5/iwi5145h/software/private/conda/envs/mri/bin/activate

cd /home/woody/iwi5/iwi5145h/Databases/dataset-registry
python scanner.py   # scans /home/janus/iwi5-datasets/, writes .registry/datasets.json

# Then push to GitHub:
bash sync.sh --no-scan   # if you just ran scanner.py above
# OR in one step:
bash sync.sh             # scan + push
```

### Step 5 — Set up automatic triggers (optional)

```bash
bash setup_cron.sh
# Installs:
#   - Weekly cron (Mon 02:00 AM) to rescan everything
#   - inotifywait daemon that detects new dataset folders and triggers sync
```

---

## Updating a dataset's metadata

If the scanner left a `???` in a dataset's entry, it will have generated a
`dataset_info.yaml` file inside that dataset's folder. Fill it in:

```bash
nano /home/janus/iwi5-datasets/DATASET_NAME/dataset_info.yaml
# edit the ??? fields, then:
bash sync.sh --dataset DATASET_NAME   # rescan that one dataset, then push
```

You can also copy the template manually:
```bash
cp templates/dataset_info.yaml /home/janus/iwi5-datasets/DATASET_NAME/
```

---

## Environment variables

| Variable        | Default                          | Description                    |
|-----------------|----------------------------------|--------------------------------|
| `DATASETS_ROOT` | `/home/janus/iwi5-datasets`      | Path to the shared datasets    |
| `OUTPUT_JSON`   | `$DATASETS_ROOT/.registry/datasets.json` | Where registry is stored |
| `PYTHON`        | `…/conda/envs/mri/bin/python`    | Python interpreter to use      |

---

## Portability — anyone can run their own instance

Any lab member can:
1. Fork this repo to their own GitHub account
2. Set `DATASETS_ROOT` to point at their local copy of the datasets
3. Run `bash sync.sh` to build and publish their own dashboard

---

## File overview

| File | Purpose |
|------|---------|
| `scanner.py` | Scans datasets, auto-detects metadata, calls APIs, writes JSON |
| `sync.sh` | Run scanner + copy JSON to repo + git push |
| `setup_cron.sh` | Install weekly cron and inotifywait watcher |
| `watch_datasets.sh` | Auto-generated watcher script (created by setup_cron.sh) |
| `web/index.html` | The GitHub Pages dashboard (no build step needed) |
| `data/datasets.json` | Registry data served to the dashboard |
| `templates/dataset_info.yaml` | Template for authors to fill in missing fields |
| `logs/` | Cron and inotify logs |
