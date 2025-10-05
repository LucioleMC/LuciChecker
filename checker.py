# fichier: check_minecraft_names.py
# Python 3.8+
# Dépendances: aiohttp, asyncio, aiofiles (optionnel), tqdm (optionnel)
# pip install aiohttp aiofiles tqdm

import asyncio
import aiohttp
import aiofiles
import sys
import argparse
from typing import Iterable, List
from asyncio import Semaphore
from tqdm import tqdm
import time
import random

MOJANG_URL = "https://api.mojang.com/users/profiles/minecraft/{}"

HEADERS = {
    "User-Agent": "OGNameChecker/1.0 (+https://example.com)",
    "Accept": "application/json"
}

ascii = r""".____                  .___________ .__                   __                 
|    |    __ __   ____ |__\_   ___ \|  |__   ____   ____ |  | __ ___________ 
|    |   |  |  \_/ ___\|  /    \  \/|  |  \_/ __ \_/ ___\|  |/ // __ \_  __ \
|    |___|  |  /\  \___|  \     \___|   Y  \  ___/\  \___|    <\  ___/|  | \/
|_______ \____/  \___  >__|\______  /___|  /\___  >\___  >__|_ \\___  >__|   
        \/           \/           \/     \/     \/     \/     \/    \/       """
print(ascii)
print("by LucioleMC")

async def check_name(session: aiohttp.ClientSession, name: str, sem: Semaphore,
                     retries:int=3, backoff_base: float = 0.5) -> (str, bool, int):
    """
    Retourne (name, available, status_code)
    available == True si le nom n'existe pas / disponible.
    """
    url = MOJANG_URL.format(name)
    async with sem:
        attempt = 0
        while True:
            try:
                async with session.get(url, headers=HEADERS, timeout=10) as resp:
                    status = resp.status
                    # 200 -> existe (donc PAS disponible)
                    # 204 -> pas de contenu / probablement pas trouvé (disponible)
                    # 404 -> selon l'API, parfois 204 ou 204-like; traiter comme non trouvé
                    if status == 200:
                        return (name, False, status)
                    elif status in (204, 404):
                        return (name, True, status)
                    elif status == 429:
                        # rate-limited: attend puis retry
                        attempt += 1
                        if attempt > retries:
                            return (name, None, status)
                        wait = backoff_base * (2 ** (attempt-1)) + random.random()*0.5
                        await asyncio.sleep(wait)
                        continue
                    else:
                        # autres codes -> on renvoie None pour available (indécis)
                        return (name, None, status)
            except asyncio.TimeoutError:
                attempt += 1
                if attempt > retries:
                    return (name, None, -1)  # timeout signal
                await asyncio.sleep(backoff_base * (2 ** (attempt-1)))
            except Exception as e:
                # erreurs réseau
                attempt += 1
                if attempt > retries:
                    return (name, None, -2)  # erreur
                await asyncio.sleep(backoff_base * (2 ** (attempt-1)))

def name_generator(chars: str, min_len: int, max_len: int) -> Iterable[str]:
    """
    Générateur simple (itératif) de combinaisons — mais ATTENTION : combinatoire exponentielle.
    Ne pas utiliser si l'espace est énorme.
    """
    if min_len <= 0:
        return
    from itertools import product
    for length in range(min_len, max_len+1):
        for combo in product(chars, repeat=length):
            yield ''.join(combo)

async def run_check(names: Iterable[str], concurrency: int, rps_limit: float,
                    output_available: str = "available.txt",
                    output_taken: str = "taken.txt",
                    show_progress: bool = True):
    """
    names: iterable of names (strings) to check
    concurrency: nombre de requêtes simultanées (par défaut 5)
    rps_limit: maximum de requêtes par seconde (float, ex: 1.0)
    """
    sem = Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit_per_host=concurrency)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = []
        # throttle via sleep entre batches si rps_limit < concurrency
        # on va lancer les tâches mais imposer un petit délai avant de démarrer chaque requête
        names_list = list(names)
        total = len(names_list)
        progress = tqdm(total=total, desc="Checking") if show_progress else None

        async def worker(name):
            # simple pacing: sleep a bit to respect rps_limit
            # distribute waits uniformly
            if rps_limit > 0:
                # compute a small random jitter to avoid burst patterns
                await asyncio.sleep(random.random() / max(1.0, rps_limit))
            res = await check_name(session, name, sem)
            if progress:
                progress.update(1)
            return res

        coros = [worker(n) for n in names_list]
        results = []
        # run with limited concurrency built into aiohttp connector + sem
        for f in asyncio.as_completed(coros):
            try:
                r = await f
                results.append(r)
            except Exception as e:
                # shouldn't happen because worker handles exceptions, mais on attrape au cas où
                results.append(("unknown", None, -2))
        if progress:
            progress.close()

        # écrire résultats
        avail_count = 0
        taken_count = 0
        unknown_count = 0
        async with aiofiles.open(output_available, "w", encoding="utf-8") as favail, \
                   aiofiles.open(output_taken, "w", encoding="utf-8") as ftaken:
            for name, available, status in results:
                if available is True:
                    await favail.write(f"{name}\t{status}\n")
                    avail_count += 1
                elif available is False:
                    await ftaken.write(f"{name}\t{status}\n")
                    taken_count += 1
                else:
                    # indécis / erreur
                    await ftaken.write(f"{name}\tUNKNOWN\t{status}\n")
                    unknown_count += 1

        print(f"Done. available: {avail_count}, taken: {taken_count}, unknown: {unknown_count}")
        print(f"Saved available -> {output_available}, taken/unknown -> {output_taken}")

def load_names_from_file(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    # sanitize: minecraft usernames allowed characters usually: a-z, A-Z, 0-9, _ (but case-insensitive)
    # convert to exact form (api is case-sensitive for endpoint, but usernames are case-insensitive)
    # keep as-is but strip spaces
    return lines

def chunk_iterable(it, limit=None):
    if limit is None:
        yield from it
    else:
        for i in range(0, len(it), limit):
            yield it[i:i+limit]

def parse_args():
    p = argparse.ArgumentParser(description="Verify disponibility of minecraft username (Mojang API).")
    p.add_argument("--file", "-f", help="File that contain words to check (one by lines).")
    p.add_argument("--generate", "-g", action="store_true", help="Generate combinations instead of use a file (not recommanded).")
    p.add_argument("--chars", default="abcdefghijklmnopqrstuvwxyz0123456789_", help="Caracters to use for generation.")
    p.add_argument("--minlen", type=int, default=2, help="Lengths minimum for the generation.")
    p.add_argument("--maxlen", type=int, default=3, help="Longths max for the generation.")
    p.add_argument("--concurrency", type=int, default=5, help="Numbers of requests simultaneous. (default : 5)")
    p.add_argument("--rps", type=float, default=1.0, help="Requests in second (global limit approximate).")
    p.add_argument("--out-available", default="available.txt")
    p.add_argument("--out-taken", default="taken.txt")
    p.add_argument("--no-progress", action="store_true", help="Don't activate the progress bar (not recommended).")
    return p.parse_args()

def main():
    args = parse_args()

    if not args.file and not args.generate:
        print("Error: use --file FILE or --generate for generate names.")
        sys.exit(1)

    if args.file:
        names = load_names_from_file(args.file)
    else:
        # avertissement si l'espace est gigantesque
        # calcul du nombre approximatif
        space = sum(len(args.chars) ** l for l in range(args.minlen, args.maxlen+1))
        print(f"Generating names with chars={len(args.chars)} chars, lengths {args.minlen}-{args.maxlen}. Total approx: {space}")
        if space > 200_000:
            print("WARNING: The generation space is > 200k. This can be long and cause deadlocks. Reduce the range or characters.")
            # on continue, mais l'utilisateur doit confirmer; comme script CLI, on laisse l'utilisateur interrompre
        gen = name_generator(args.chars, args.minlen, args.maxlen)
        names = list(gen)

    # lancer l'event loop
    try:
        asyncio.run(run_check(names, concurrency=args.concurrency, rps_limit=args.rps,
                              output_available=args.out_available,
                              output_taken=args.out_taken,
                              show_progress=not args.no_progress))
    except KeyboardInterrupt:
        print("Stopped by user.")

if __name__ == "__main__":
    main()
