#!/usr/bin/env python3
"""
perplexity_to_obsidian.py
Export de tous tes fils Perplexity -> Markdown + YAML frontmatter pour Obsidian.

Usage :
  1. Renseigne la variable COOKIE ci-dessous avec ton cookie de session
  2. python3 perplexity_to_obsidian.py -o ~/Notes/Perplexity/
  3. Options :
       --test          Exporte seulement les 3 premiers fils
       --overwrite     Ecrase les fichiers existants
       --no-detail     Utilise seulement les donnees de la liste (plus rapide, contenu tronque)
       --date-from     YYYY-MM-DD : n'exporter que les fils depuis cette date
       --title-contains  Filtrer par mot dans le titre
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

# ==============================================================================
# CONFIGURATION - remplace par ton cookie complet (guillemets simples)
# ==============================================================================
COOKIE = 'cf_clearance=gdYyik8EfTshAfBdXpX8Gf0X2L47t3OPWms7120YKcY-1773333575-1.2.1.1-RE08hVtxQXH.kh.shLW1RC7DY52A3ZUXZfly6MARBmcmV4_M3i2FRvIng4b_Db76BPIWiifg4McKT5eBX5Vt6m4cLNIiWdrnAsLJ1cHWYxyxwMws86aMiE2esbos6p6GSrnDaSlLiC_5_0rJhuYXt02cBJgKInYA6tWelX4W3z8MI40em_WKnFVCfdgE8tf9YKjy35hJLOj0n9ka0knXUj8krvBS5ZpD2Ng94QmgX7k; pplx.edge-sid=86d679cf-5e49-41d0-add6-72f68eb74868; pplx.edge-vid=2fedbb45-ecf2-4bbd-82ba-cc107aebb795; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..pZcoLZhNaEPuo1qB.IjONS0tZ3IGEsGOZ9RN9qmimErGNTRVvHO1bdK1Tf-T_P_1Q6AAxi3D4KTbJhiThlyg6dhee2FtMW9cZkhbkoGLENHelcqoCtTLduFBjKbnR5isp78vn_0U9tv7pOUbtwjrgAwjD091ZK8xR6MOakdRK7rJdI_-crIznlqOCndrov_rHGc5zSZllyvN4hum866ghQsozQFza5UdxWzNA0mz3sESOuo_Xh7dEJQWh6tIFKTymjA-aLGADnYHoe1rsz6roeB9o2mNPY_3t7tYEeVzrJ0DVOpXxzqEop0R4Jxa8IGmEXTR4r6B5PwB3bri3rYUT6c66TmBkTxMrlLYblbsUSFtMxdU5tAr2R9MKbvfjoNU9cBc7hbhjqV9JY0xxTSuQXbcKcaqH5MkllVEl.Tnemwqye4nOMnNQtJDlHvg; pplx.metadata={%22qc%22:0%2C%22qcu%22:2377%2C%22qcm%22:694%2C%22qcc%22:1943%2C%22qcco%22:0%2C%22qccol%22:0%2C%22qcdr%22:0%2C%22qcs%22:0%2C%22qcg%22:0%2C%22qcd%22:0%2C%22hli%22:true%2C%22hcga%22:false%2C%22hcds%22:false%2C%22hso%22:false%2C%22hfo%22:false%2C%22hsco%22:false%2C%22hfco%22:false%2C%22hsma%22:false%2C%22hdc%22:false%2C%22hdttb%22:false}; pplx.session-id=a208e9f4-f0b4-4281-838f-7191585b2341; _dd_s=aid=f481630c-a9dd-4a97-8d1d-ed2d5caaddfb&rum=2&id=ef5a6990-faf8-4d98-94e7-81f0cd320d49&created=1773332288542&expire=1773334474694&logs=0; __cf_bm=krjKybnUWh6IDK3sKOGEUbd6Bevzy.Mtk43XOB4JrJ8-1773333190-1.0.1.1-hNSCnpvFWpZUDqJ9I9VDUT4ImQZUxxdbzVvN6Aam1WmCUlQ9joSJ3waG_ZnTvzWRRuwwpljHGYegxJ2uDX9XN.6SK1LxUn_E7SyW8u0OVqQ; g_state={"i_l":0,"i_ll":1773332297734,"i_b":"SqDdQZzX6iSgrVRhzm+68W1hId+yb4Z+J+XezfnpAWE","i_e":{"enable_itp_optimization":0}}; next-auth.csrf-token=04e7d7a0cd19a63bdc921d54a80e166a2d0d49e36713ff809bb228520ff6993e%7C297c324e18cef51f537b928a9d7f81b8db8d0f0eee2cd88904c9e99674eeb49a; __cflb=02DiuDyvFMmK5p9jVbVnMNSKYZhUL9aGm1rRWGxRxmu1e; _ga_SH9PRBQG23=GS2.1.s1773307281$o2$g0$t1773307285$j56$l0$h0; _ga=GA1.1.442844348.1773064780; pplx.visitor-id=7c5b2584-5844-44b7-a78d-4b201756deee; cf_clearance=m5jEL16dYHardiwgRbchGHQGEKhr6iqSihpkmQ5rTwQ-1761810548-1.2.1.1-ly6UzpUaG35PuTMazb.l6kghAnuuIABFKFRep2krfilwv1Nj8Rqx8LmSsOXf0CKr6Y4TFGgo.MXOTUc7G3AodNtz5OgWrepdJ4goiS2FyP1X50oi0Cjhs0AOcMWtjBQUjFV9grSiDw5WQAp.Ovag3o3nuuZJ8ytC4p8pJBuGWdkFwSTqmiqACYtTzWagiQN1rB7oOwbaDv3mhREW88HZWz8wG6f1s4k26TtTGsONXU4'

API_VERSION = "2.18"
BASE_URL    = "https://www.perplexity.ai"
DELAY       = 0.5   # secondes entre requetes
# ==============================================================================


HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3 Safari/605.1.15",
    "Accept":          "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Content-Type":    "application/json",
    "Origin":          BASE_URL,
    "Referer":         f"{BASE_URL}/library",
    "x-app-apiversion":                API_VERSION,
    "x-app-apiclient":                 "default",
    "x-perplexity-request-reason":     "threads-body",
    "x-perplexity-request-try-number": "1",
    "Cookie":          COOKIE,
}


# ------------------------------------------------------------------ date

def parse_date(s) -> datetime | None:
    if not s:
        return None
    s = str(s)[:26]
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s[:len(fmt) + 2], fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ API : liste

def fetch_thread_list(limit: int = 50) -> list:
    url     = f"{BASE_URL}/rest/thread/list_ask_threads?version={API_VERSION}&source=default"
    threads = []
    offset  = 0

    while True:
        payload = {
            "limit":       limit,
            "ascending":   False,
            "offset":      offset,
            "search_term": "",
            "exclude_asi": False,
        }
        r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()

        batch = data if isinstance(data, list) else (
            data.get("threads") or data.get("items") or data.get("data") or []
        )
        if not batch:
            break

        threads.extend(batch)

        total    = batch[0].get("total_threads", len(threads)) if batch else len(threads)
        has_next = batch[0].get("has_next_page", False) if batch else False
        print(f"  Recupere {len(threads)}/{total} fils...")

        if not has_next:
            break
        offset += limit
        time.sleep(DELAY)

    return threads


# ------------------------------------------------------------------ API : detail

def fetch_thread_detail(uuid: str) -> dict | list:
    url = f"{BASE_URL}/rest/thread/{uuid}?version={API_VERSION}"
    r   = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ parsing du detail

def extract_answer(raw) -> str:
    """Extrait le texte de la reponse depuis differents formats possibles."""
    if isinstance(raw, str):
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
                return obj.get("answer") or obj.get("text") or obj.get("content") or raw
            except json.JSONDecodeError:
                pass
        return raw
    if isinstance(raw, dict):
        return raw.get("answer") or raw.get("text") or raw.get("content") or str(raw)
    return str(raw) if raw else ""


def extract_messages_from_detail(detail) -> list:
    messages = []
    steps = detail if isinstance(detail, list) else []
    if not steps and isinstance(detail, dict):
        for key in ("entries", "steps", "threads", "items"):
            v = detail.get(key)
            if isinstance(v, list) and v:
                steps = v
                break

    # Format 1 : steps avec step_type INITIAL_QUERY / FINAL
    for step in steps:
        if not isinstance(step, dict):
            continue
        stype   = step.get("step_type", "")
        content = step.get("content") or {}
        if stype == "INITIAL_QUERY":
            q = content.get("query") or content.get("text") or ""
            if q:
                messages.append({"role": "Human", "content": q.strip()})
        elif stype == "FINAL":
            raw = content.get("answer") or content.get("text") or ""
            a   = extract_answer(raw)
            if a:
                messages.append({"role": "Perplexity", "content": a.strip()})

    if messages:
        return messages

    # Format 2 : liste d'items avec query/answer directs
    for item in steps:
        if not isinstance(item, dict):
            continue
        q = item.get("query") or item.get("question") or item.get("user_message") or ""
        a = extract_answer(item.get("answer") or item.get("text") or item.get("assistant_message") or "")
        if q:
            messages.append({"role": "Human",      "content": q.strip()})
        if a:
            messages.append({"role": "Perplexity", "content": a.strip()})

    return messages


# ------------------------------------------------------------------ slugify

def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text[:max_len]


# ------------------------------------------------------------------ Markdown

def thread_to_markdown(item: dict, messages: list, extra_tags: list) -> str:
    title    = (item.get("title") or item.get("slug") or "Sans titre").strip()
    uuid     = item.get("uuid", "")
    slug     = item.get("slug", "")
    dt       = parse_date(item.get("last_query_datetime") or item.get("created_at"))
    date_str = dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else ""
    sources  = item.get("sources") or []
    mode     = item.get("display_mode") or ""

    tags = ["perplexity", "ia/conversation"] + extra_tags
    if "web" in sources:
        tags.append("ia/web-search")

    tags_block = "\n  - ".join(tags)

    yaml_fm = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f"date: {date_str}\n"
        f"tags:\n  - {tags_block}\n"
        "source: perplexity\n"
        f"thread_id: {uuid}\n"
        f"slug: {slug}\n"
        f"model: {mode}\n"
        "---\n\n"
    )

    body = f"# {title}\n\n"
    for msg in messages:
        label = "## Human" if msg["role"] == "Human" else "## Perplexity"
        body += f"{label}\n\n{msg['content']}\n\n---\n\n"

    return yaml_fm + body


# ------------------------------------------------------------------ filtrage

def filter_threads(threads: list, args) -> list:
    result = []
    for t in threads:
        if args.title_contains:
            title = (t.get("title") or t.get("slug") or "").lower()
            if args.title_contains.lower() not in title:
                continue
        if args.date_from:
            dt = parse_date(t.get("last_query_datetime") or t.get("created_at"))
            if dt is None:
                continue
            lim = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt < lim:
                continue
        result.append(t)
    return result


# ------------------------------------------------------------------ main

def build_parser():
    p = argparse.ArgumentParser(
        description="Export Perplexity -> Markdown Obsidian",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--output-dir", "-o", default="./perplexity_export")
    p.add_argument("--title-contains", "-t")
    p.add_argument("--date-from")
    p.add_argument("--tags", nargs="*", default=[])
    p.add_argument("--test",       action="store_true", help="3 premiers fils seulement")
    p.add_argument("--no-detail",  action="store_true", help="Pas d'appel detail (contenu tronque)")
    p.add_argument("--overwrite",  action="store_true")
    p.add_argument("--dry-run", "-n", action="store_true")
    return p


def main():
    args       = build_parser().parse_args()
    output_dir = Path(args.output_dir).expanduser()

    print("Recuperation de la liste des fils...")
    try:
        all_threads = fetch_thread_list(limit=50)
    except requests.HTTPError as e:
        print(f"Erreur HTTP {e.response.status_code} : verifiez votre cookie.", file=sys.stderr)
        sys.exit(1)
    print(f"{len(all_threads)} fils recuperes au total.")

    threads = filter_threads(all_threads, args)
    print(f"{len(threads)} fils apres filtrage.")

    if args.test:
        threads = threads[:3]
        print("Mode test : seulement les 3 premiers.")

    if not threads:
        print("Aucun fil a exporter.")
        return

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    written = skipped = errors = 0

    for i, item in enumerate(threads, 1):
        title    = (item.get("title") or item.get("slug") or "sans-titre").strip()
        uuid     = item.get("uuid", f"thread-{i}")
        dt       = parse_date(item.get("last_query_datetime") or item.get("created_at"))
        date_pfx = dt.strftime("%Y-%m-%d") if dt else "0000-00-00"
        filename = f"{date_pfx}_{slugify(title)}.md"
        out_path = output_dir / filename

        if out_path.exists() and not args.overwrite:
            print(f"  [ignore]  {filename}")
            skipped += 1
            continue

        # Contenu : detail si possible, sinon fallback liste
        if args.no_detail:
            messages = messages_from_list_item(item)
        else:
            try:
                time.sleep(DELAY)
                detail   = fetch_thread_detail(uuid)
                messages = extract_messages_from_detail(detail)
                if not messages:
                    messages = messages_from_list_item(item)
            except Exception as e:
                print(f"  [warn]    Detail indisponible pour '{title[:40]}' : {e}")
                messages = messages_from_list_item(item)
                errors  += 1

        md = thread_to_markdown(item, messages, args.tags)

        if args.dry_run:
            print(f"  [dry-run] {filename}  ({len(messages)} messages)")
        else:
            out_path.write_text(md, encoding="utf-8")
            print(f"  [ok {i:>3}/{len(threads)}]  {filename}  ({len(messages)} messages)")
        written += 1

    label = "[dry-run] " if args.dry_run else ""
    print(f"\n{label}Termine : {written} fichier(s), {skipped} ignore(s), {errors} avertissement(s).")
    if not args.dry_run and written:
        print(f"Dossier : {output_dir.resolve()}")


if __name__ == "__main__":
    main()
