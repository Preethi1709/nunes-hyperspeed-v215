from __future__ import annotations
import argparse, shutil, subprocess, sys

MODELS=('qwen3:1.7b','qwen3:4b','qwen3-vl:4b')

def run(args, capture=False):
    return subprocess.run(args, text=True, capture_output=capture)

def main():
    ap=argparse.ArgumentParser(description='Check or install V21.5 local exception models in Ollama.')
    ap.add_argument('--check-only',action='store_true')
    args=ap.parse_args()
    exe=shutil.which('ollama')
    if not exe:
        print('OLLAMA NOT FOUND. V21.5 Python lanes still work; local AI exceptions will remain optional.')
        return 2 if args.check_only else 1
    r=run([exe,'list'],capture=True)
    names=set()
    if r.returncode==0:
        for line in r.stdout.splitlines()[1:]:
            if line.strip(): names.add(line.split()[0])
    missing=[]
    for model in MODELS:
        ok=model in names or any(n.startswith(model+'-') for n in names)
        print(('READY ' if ok else 'MISSING ')+model)
        if not ok: missing.append(model)
    if not missing:
        print('V21.5 MODEL CHECK: READY')
        return 0
    if args.check_only:
        print('V21.5 MODEL CHECK: OPTIONAL MODELS MISSING')
        return 2
    for model in missing:
        print(f'\nPulling {model}...')
        r=run([exe,'pull',model])
        if r.returncode!=0:
            print(f'FAILED TO PULL {model}. Core Python processing can still run.')
    print('\nModel setup finished. Re-run with --check-only to verify.')
    return 0

if __name__=='__main__': raise SystemExit(main())
