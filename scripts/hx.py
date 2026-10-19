#!/usr/bin/env python3
"""Helix for AROS: prepare patched sources and build for x86_64 ABIv11.

Commands (run `hx.py COMMAND --help`):
  config       show the resolved settings
  materialize  create work/helix from the pin and patches/helix/series
  export       write the work copies' commits to patches/ (helix and crates)
  verify       rebuild every series in a temporary directory and compare trees
  build        build hx from the work copy into the build root

Nothing here edits upstream/helix, a shared toolchain, the SDK or the Cargo
registry. Existing work copies are never reset or recreated.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UPSTREAMS = REPO / "upstreams.json"
PATCHES = REPO / "patches"
CRATES = PATCHES / "crates"
PTHREAD_DIR = PATCHES / "aros-pthread"
WORK = REPO / "work"
# The SDK's libpthread.a objects are reproduced exactly by these flags (checked below).
PTHREAD_CFLAGS = ["-O2", "-mcmodel=large", "-fno-omit-frame-pointer"]
# The members patches/aros-pthread changes; only these are linked from the private build.
PTHREAD_PATCHED = ("pthread", "pthread_create")
AUTHOR = ["-c", "user.name=tomaszstaniak", "-c", "user.email=68373859+tomaszstaniak@users.noreply.github.com"]

# Grammars linked into hx, since AROS cannot load shared libraries: name ->
# upstreams.json id. The revision must be the one Helix's languages.toml names.
GRAMMARS = {"rust": "tree-sitter-rust"}

# key: (environment variable, default, description)
SETTINGS = {
    "aros_gcc": ("HX_AROS_GCC", None, "x86_64 AROS C compiler driver matching the SDK"),
    "aros_ar": ("HX_AROS_AR", None, "archiver from the same toolchain"),
    "aros_sdk": ("HX_AROS_SDK", None, "ABIv11 SDK used as --sysroot"),
    "aros_src": ("HX_AROS_SRC", None, "deadwood2/AROS checkout; read-only source of the pthread link library"),
    "aros_src_commit": ("HX_AROS_SRC_COMMIT", "5376f09bc1ed0c36a12fbb096406013f8a046e89",
                        "commit whose compiler/pthread matches the SDK's libpthread.a"),
    "rust_aros": ("HX_RUST_AROS", None, "rust-aros Git checkout (target template, libc-aros, libc-dual.py)"),
    "rust_aros_commit": ("HX_RUST_AROS_COMMIT", None, "rust-aros commit to build from"),
    "toolchain": ("HX_TOOLCHAIN", None, "rustup name of the private AROS std toolchain"),
    "rustc_version": ("HX_RUSTC_VERSION", "rustc 1.100.0-nightly (0dfb098f3 2026-08-31)",
                      "exact `rustc --version` the toolchain must report"),
    "cargo_home": ("HX_CARGO_HOME", str(Path.home() / ".cargo"), "CARGO_HOME holding the registry cache"),
    "build_root": ("HX_BUILD_ROOT", "build/v11", "build outputs (relative: to the repository)"),
}


class Failure(Exception):
    pass


def config(required=()):
    path = Path(os.environ.get("HX_LOCAL", REPO / "local.json"))
    local = json.loads(path.read_text()) if path.exists() else {}
    unknown = set(local) - set(SETTINGS)
    if unknown:
        raise Failure(f"{path}: unknown keys {sorted(unknown)}")
    out = {}
    for key, (env, default, _) in SETTINGS.items():
        value = os.environ.get(env) or local.get(key) or default
        if key in ("build_root",) and value and not Path(value).is_absolute():
            value = str(REPO / value)
        out[key] = value
    missing = [f"{k} ({SETTINGS[k][0]} or local.json)" for k in required if not out.get(k)]
    if missing:
        raise Failure("missing settings: " + ", ".join(missing))
    return out


def run(cmd, cwd=None, env=None, capture=True, check=True):
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=False,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if check and result.returncode:
        detail = (result.stderr or b"").decode(errors="replace").strip()
        raise Failure(f"{' '.join(map(str, cmd))} failed ({result.returncode}){': ' + detail if detail else ''}")
    return result


def git(repo, *args, **kw):
    return run(["git", "-C", repo, *AUTHOR, *args], **kw)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file() and ".git" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"x" if os.access(path, os.X_OK) else b"-")
    return digest.hexdigest()


def series(directory):
    path = Path(directory) / "series"
    if not path.exists():
        raise Failure(f"{path} is missing (an empty file is a valid empty series)")
    return [Path(directory) / name for name in path.read_text().split()]


def upstream_pin():
    data = json.loads(UPSTREAMS.read_text())["repositories"]["helix"]
    return data["url"], data["commit"]


def crate_specs():
    return json.loads((CRATES / "crates.json").read_text())["crates"]


def crate_archive(cfg, spec):
    name = f"{spec['name']}-{spec['version']}.crate"
    found = list(Path(cfg["cargo_home"]).glob(f"registry/cache/*/{name}"))
    if not found:
        raise Failure(f"{name} not in the Cargo cache; run `cargo fetch` in work/helix")
    # Older registry indexes keep their own cache; any copy with the recorded digest will do.
    for archive in found:
        if sha256_file(archive) == spec["checksum"]:
            return archive
    raise Failure(f"no {name} in the Cargo cache matches its recorded checksum")


def apply_patches(dest, patches):
    for patch in patches:
        run(["git", "-C", dest, *AUTHOR, "am", "--quiet", "--keep-cr", patch])


# --- work copies --------------------------------------------------------------

def cmd_materialize(args):
    url, commit = upstream_pin()
    upstream = REPO / "upstream" / "helix"
    if not upstream.exists():
        raise Failure(f"clone {url} into {upstream} first (git clone {url} {upstream})")
    if git(upstream, "status", "--porcelain").stdout.strip():
        raise Failure(f"{upstream} has local changes; it must stay clean")
    git(upstream, "cat-file", "-e", commit + "^{commit}")
    dest = WORK / "helix"
    if dest.exists():
        raise Failure(f"{dest} exists; it is never recreated here (export or move it first)")
    run(["git", "clone", "--quiet", "--no-checkout", upstream, dest])
    git(dest, "checkout", "--quiet", "-b", "aros", commit)
    apply_patches(dest, series(PATCHES / "helix"))
    for spec in crate_specs():
        ident = f"{spec['name']}-{spec['version']}"
        target = WORK / "crates" / ident
        if target.exists():
            raise Failure(f"{target} exists; it is never recreated here")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(crate_archive(config(["cargo_home"]), spec)) as tar:
            tar.extractall(target.parent, filter="data")
        run(["git", "-C", target, "init", "--quiet"])
        git(target, "add", "-A")
        git(target, "commit", "--quiet", "-m", f"{ident} from crates.io")
        apply_patches(target, series(CRATES / ident))
    print(f"materialized {dest} and {len(crate_specs())} crates")


def export_repo(repo, base, out_dir):
    """Writes the commits after `base` as numbered patches and the series."""
    status = git(repo, "status", "--porcelain").stdout.decode().strip()
    if status:
        raise Failure(f"{repo} has uncommitted changes:\n{status}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        git(repo, "format-patch", "--quiet", "--no-signature", "--zero-commit", "--full-index",
            "-o", tmp, f"{base}..HEAD")
        names = sorted(p.name for p in Path(tmp).glob("*.patch"))
        for old in out_dir.glob("*.patch"):
            if old.name not in names:
                old.unlink()  # retired; the repository history keeps it
        for name in names:
            shutil.copyfile(Path(tmp) / name, out_dir / name)
        (out_dir / "series").write_text("".join(n + "\n" for n in names))
    return names


def cmd_export(args):
    _, commit = upstream_pin()
    names = export_repo(WORK / "helix", commit, PATCHES / "helix")
    print(f"helix: {len(names)} patches")
    for spec in crate_specs():
        ident = f"{spec['name']}-{spec['version']}"
        repo = WORK / "crates" / ident
        root = git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.decode().split()[0]
        names = export_repo(repo, root, CRATES / ident)
        print(f"{ident}: {len(names)} patches")


def cmd_verify(args):
    """Rebuilds every series from its pinned base and compares with the work copies."""
    cfg = config(["cargo_home"])
    _, commit = upstream_pin()
    with tempfile.TemporaryDirectory(prefix="hx-verify-") as tmp:
        tmp = Path(tmp)
        rebuilt = tmp / "helix"
        run(["git", "clone", "--quiet", "--no-checkout", REPO / "upstream" / "helix", rebuilt])
        git(rebuilt, "checkout", "--quiet", "-b", "verify", commit)
        apply_patches(rebuilt, series(PATCHES / "helix"))
        pairs = [(rebuilt, WORK / "helix")]
        for spec in crate_specs():
            ident = f"{spec['name']}-{spec['version']}"
            with tarfile.open(crate_archive(cfg, spec)) as tar:
                tar.extractall(tmp / "crates", filter="data")
            copy = tmp / "crates" / ident
            run(["git", "-C", copy, "init", "--quiet"])
            git(copy, "add", "-A")
            git(copy, "commit", "--quiet", "-m", "base")
            apply_patches(copy, series(CRATES / ident))
            pairs.append((copy, WORK / "crates" / ident))
        bad = []
        for copy, work in pairs:
            committed = git(work, "rev-parse", "HEAD^{tree}").stdout.strip()
            if git(copy, "rev-parse", "HEAD^{tree}").stdout.strip() != committed:
                bad.append(work.name)
        if bad:
            raise Failure("series do not reproduce the work copies' commits: " + ", ".join(bad))
    print("every series reproduces its work copy's last commit")


# --- build ---------------------------------------------------------------------

def stage_tree(repo, dest):
    """Copies the committed tree of `repo` (not its uncommitted edits)."""
    archive = git(repo, "archive", "--format=tar", "HEAD").stdout
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def build_pthread(cfg, root):
    """libpthread objects from deadwood2/AROS compiler/pthread with patches/aros-pthread
    applied. The unpatched tree must first rebuild the SDK's archive object for object,
    so the patches are all that differs. Adapted from the PdfCraft port."""
    patches = series(PTHREAD_DIR)
    commit = cfg["aros_src_commit"]
    inputs = {"source": commit, "patches": {p.name: sha256_file(p) for p in patches},
              "sdk_archive": sha256_file(Path(cfg["aros_sdk"]) / "lib/libpthread.a"),
              "gcc": sha256_file(cfg["aros_gcc"]), "cflags": PTHREAD_CFLAGS}
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
    out = root / f"aros-pthread-{commit[:12]}-{key}"
    if (out / "pthread.json").exists():
        record = json.loads((out / "pthread.json").read_text())
        if record["inputs"] != inputs or any(sha256_file(out / n) != d for n, d in record["objects"].items()):
            raise Failure(f"changed pthread cache {out}; keep it for inspection, use a new build root")
        return out
    archive = run(["git", "-C", cfg["aros_src"], "archive", "--format=tar", commit, "compiler/pthread"]).stdout
    gcc, ar = cfg["aros_gcc"], cfg["aros_ar"]
    with tempfile.TemporaryDirectory(prefix="pthread-", dir=root) as tmp:
        tmp = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        src = tmp / "compiler/pthread"
        names, collecting = [], False
        for line in (src / "mmakefile.src").read_text().splitlines():
            if line.startswith(("LINKLIBFILES", "PTHREADFUNCS")):
                collecting = True
                line = line.split(":=", 1)[1]
            if collecting:
                names += [w for w in line.replace("\\", " ").split() if w]
                collecting = line.rstrip().endswith("\\")
        sdk_objs = tmp / "sdk"
        sdk_objs.mkdir()
        run([ar, "x", Path(cfg["aros_sdk"]) / "lib/libpthread.a"], cwd=sdk_objs)
        if sorted(p.stem for p in sdk_objs.glob("*.o")) != sorted(names):
            raise Failure("SDK libpthread.a members differ from the recipe")

        def disassembly(obj):
            text = run([gcc.replace("-gcc", "-objdump"), "-d", "-r", "--no-show-raw-insn", obj]).stdout
            return hashlib.sha256(text.decode().split("\n", 4)[-1].encode()).hexdigest()

        for stage in ("plain", "patched"):
            if stage == "patched":
                for patch in patches:
                    run(["patch", "--quiet", "-p3", "-i", patch], cwd=src)
            (tmp / stage).mkdir()
            for name in names:
                run([gcc, f"--sysroot={cfg['aros_sdk']}", *PTHREAD_CFLAGS, "-I", src, "-c",
                     src / f"{name}.c", "-o", tmp / stage / f"{name}.o"])
            if stage == "plain":
                bad = [n for n in names if disassembly(tmp / "plain" / f"{n}.o") != disassembly(sdk_objs / f"{n}.o")]
                if bad:
                    raise Failure(f"{commit} compiler/pthread does not rebuild the SDK's objects: {bad}")
        changed = [n for n in names if disassembly(tmp / "patched" / f"{n}.o") != disassembly(tmp / "plain" / f"{n}.o")]
        if sorted(changed) != sorted(PTHREAD_PATCHED):
            raise Failure(f"the pthread patches change {changed}, expected {list(PTHREAD_PATCHED)}")
        staged = tmp / "out"
        staged.mkdir()
        for n in PTHREAD_PATCHED:
            shutil.copyfile(tmp / "patched" / f"{n}.o", staged / f"{n}.o")
        record = {"inputs": inputs, "objects": {f"{n}.o": sha256_file(staged / f"{n}.o") for n in PTHREAD_PATCHED}}
        (staged / "pthread.json").write_text(json.dumps(record, indent=2) + "\n")
        staged.rename(out)
    return out


def build_grammars(cfg, root):
    """Compiles each linked grammar (parser.c, scanner.c) and the wctype fallback
    its scanner needs into one archive."""
    repos = json.loads(UPSTREAMS.read_text())["repositories"]
    out = root / "grammars"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shim = REPO / "src/grammars"
    flags = [f"--sysroot={cfg['aros_sdk']}", "-O2", "-fno-PIC", "-std=c11", "-w"]
    objects = [out / "wctype_aros.o"]
    run([cfg["aros_gcc"], *flags, "-c", shim / "wctype_aros.c", "-o", objects[0]])
    for name, ident in GRAMMARS.items():
        checkout = REPO / "upstream" / ident
        commit = repos[ident]["commit"]
        if git(checkout, "rev-parse", "HEAD").stdout.decode().strip() != commit:
            raise Failure(f"{checkout} is not at the pinned {commit}")
        if git(checkout, "status", "--porcelain").stdout.strip():
            raise Failure(f"{checkout} has local changes; it must stay clean")
        src = checkout / "src"
        for part in ("parser", "scanner"):
            if (src / f"{part}.c").exists():
                obj = out / f"{name}-{part}.o"
                run([cfg["aros_gcc"], *flags, "-include", shim / "wctype_aros.h", "-I", src,
                     "-c", src / f"{part}.c", "-o", obj])
                objects.append(obj)
    archive = out / "libhelix-grammars.a"
    run([cfg["aros_ar"], "rcs", archive, *objects])
    return archive


def cmd_build(args):
    cfg = config(["aros_gcc", "aros_ar", "aros_sdk", "aros_src", "rust_aros", "rust_aros_commit", "toolchain"])
    for name in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS", "CARGO_TARGET_DIR"):
        if os.environ.get(name):
            raise Failure(f"clear {name}: it would override the selected profile or output")
    rustc = run(["rustc", "+" + cfg["toolchain"], "--version"]).stdout.decode().strip()
    if rustc != cfg["rustc_version"]:
        raise Failure(f"toolchain {cfg['toolchain']} reports `{rustc}`, expected `{cfg['rustc_version']}`")
    root = Path(cfg["build_root"])
    root.mkdir(parents=True, exist_ok=True)

    src = root / "src"
    stage_tree(WORK / "helix", src)
    # Helix sets its flags as target."cfg(all())".rustflags, which would replace
    # the AROS profile below rather than merge with it; its tokio_unstable is
    # passed explicitly instead, and -crt-static does not apply to AROS.
    (src / ".cargo/config.toml").unlink()
    head = git(WORK / "helix", "rev-parse", "HEAD").stdout.decode().strip()

    rust_aros, rcommit = Path(cfg["rust_aros"]), cfg["rust_aros_commit"]
    git(rust_aros, "cat-file", "-e", rcommit + "^{commit}")
    rtree = root / f"rust-aros-{rcommit[:12]}"
    if not rtree.exists():
        archive = run(["git", "-C", rust_aros, "archive", "--format=tar", rcommit,
                       "std-groundwork/libc-aros", "tools/libc-dual.py", "x86_64-aros.json.in"]).stdout
        with tempfile.TemporaryDirectory(prefix="rust-aros-", dir=root) as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tmp, filter="data")
            Path(tmp).rename(rtree)
    libc = root / f"libc-dual-{rcommit[:12]}"
    if not libc.exists():
        run([sys.executable, rtree / "tools/libc-dual.py", rtree / "std-groundwork/libc-aros", libc,
             "--cargo-home", cfg["cargo_home"]])
    template = (rtree / "x86_64-aros.json.in").read_text()
    target_json = root / "x86_64-aros.json"
    text = (template.replace("@AROS_GCC@", cfg["aros_gcc"]).replace("@AROS_SDK@", cfg["aros_sdk"])
            .replace("@AROS_EXTRA_LIBS@", ""))
    if not target_json.exists() or target_json.read_text() != text:
        target_json.write_text(text)

    crates = {}
    for spec in crate_specs():
        ident = f"{spec['name']}-{spec['version']}"
        dest = root / "crates" / ident
        stage_tree(WORK / "crates" / ident, dest)
        crates[spec["name"]] = dest
    pthread = build_pthread(cfg, root)
    grammars = build_grammars(cfg, root)

    sysroot = run(["rustc", "+" + cfg["toolchain"], "--print", "sysroot"]).stdout.decode().strip()
    # getrandom 0.3 has no AROS backend: helix-stdx supplies the custom one.
    flags = ["-Cpanic=" + args.panic, "--cfg", 'getrandom_backend="custom"', "--cfg", "tokio_unstable",
             f"--remap-path-prefix={src}=/helix", f"--remap-path-prefix={cfg['cargo_home']}=/cargo",
             f"--remap-path-prefix={libc}=/libc-aros", f"--remap-path-prefix={sysroot}=/rust"]
    # The AROS GCC driver searches the SDK's lib/ before any -L, so a patched
    # libpthread.a cannot win the -lpthread lookup. The two patched objects go first
    # on the link line instead: they define every symbol of the SDK's pthread.o and
    # pthread_create.o, so -lpthread pulls neither.
    if args.panic == "unwind":
        flags.append("-Cforce-unwind-tables=yes")
    flags.append("-Zpre-link-args=" + " ".join(str(pthread / f"{n}.o") for n in PTHREAD_PATCHED))
    if not args.probe:
        # helix-loader refers to tree_sitter_<name> for each grammar in GRAMMARS.
        flags.append(f"-Clink-arg={grammars}")
    patch = ["--config", "patch.crates-io.libc.path=" + json.dumps(str(libc))]
    for name, path in crates.items():
        patch += ["--config", f"patch.crates-io.{name}.path=" + json.dumps(str(path))]
    env = dict(os.environ, AROS_GCC=cfg["aros_gcc"], AROS_SDK=cfg["aros_sdk"], AROS_AR=cfg["aros_ar"],
               CARGO_HOME=cfg["cargo_home"], CARGO_TARGET_DIR=str(root / "target"),
               HELIX_DISABLE_AUTO_GRAMMAR_BUILD="1",
               # The C parts (tree-sitter's runtime) for the cc crate.
               CC_x86_64_aros=cfg["aros_gcc"], AR_x86_64_aros=cfg["aros_ar"],
               # The cc crate asks for PIC; AROS executables are static, and PIC
               # objects reference a _GLOBAL_OFFSET_TABLE_ the link never has.
               CFLAGS_x86_64_aros=f"--sysroot={cfg['aros_sdk']} -fno-PIC")
    cargo = ["cargo", "+" + cfg["toolchain"]]
    # The lock pins upstream libc 0.2.174; the AROS libc is 0.2.189.
    run(cargo + ["update", "--offline", "-p", "libc@0.2.174", "--precise", "0.2.189"] + patch,
        cwd=src, env=env, capture=False, check=False)
    if args.probe:
        # experiments/<probe>: the same profile and patched crates, its own lock.
        probe_src = root / "probes" / args.probe
        if probe_src.exists():
            shutil.rmtree(probe_src)
        shutil.copytree(REPO / "experiments" / args.probe, probe_src,
                        ignore=shutil.ignore_patterns("target", "Cargo.lock"))
        run(cargo + ["generate-lockfile", "--offline"] + patch, cwd=probe_src, env=env, capture=False, check=False)
        command = cargo + ["build", "--release", "--offline", "--target", target_json,
                           "-Zbuild-std=std,panic_" + args.panic, "-Zjson-target-spec",
                           "--config", "build.rustflags=" + json.dumps(flags)] + patch
        result = run(command, cwd=probe_src, env=dict(env, CARGO_TARGET_DIR=str(root / "probe-target")),
                     capture=False, check=False)
        if result.returncode:
            raise Failure(f"cargo build failed ({result.returncode})")
        print(root / "probe-target/x86_64-aros/release" / args.probe)
        return
    command = cargo + ["build", "--release", "--offline", "-p", "helix-term", "--no-default-features",
                       "--target", target_json, "-Zbuild-std=std,panic_" + args.panic, "-Zjson-target-spec",
                       "--config", "build.rustflags=" + json.dumps(flags)] + patch
    result = run(command, cwd=src, env=env, capture=False, check=False)
    if result.returncode:
        raise Failure(f"cargo build failed ({result.returncode})")
    binary = root / "target/x86_64-aros/release/hx"
    # Licences of everything compiled into hx, for the archive.
    tree = run(cargo + ["tree", "--offline", "-p", "helix-term", "--no-default-features", "-e", "normal",
                        "--prefix", "none", "--format", "{p}: {l}", "--target", target_json,
                        "-Zjson-target-spec"] + patch, cwd=src, env=env).stdout.decode()
    # Drop local paths and "(*)" marks: "helix-core v25.7.1 (/...): MPL-2.0".
    lines = sorted({re.sub(r" \([^)]*\)", "", line) for line in tree.splitlines() if line.strip()})
    for name, ident in GRAMMARS.items():
        repo = json.loads(UPSTREAMS.read_text())["repositories"][ident]
        lines.append(f"{ident} {repo['commit'][:12]}: {repo['license']}")
    (root / "THIRD-PARTY.txt").write_text(
        "Helix for AROS includes these components, under the licences their authors chose.\n"
        "Their full texts are in each component's source.\n\n" + "\n".join(lines) + "\n")
    record = {"helix_work_head": head, "rust_aros_commit": rcommit, "toolchain": rustc,
              "crates": {n: tree_hash(p) for n, p in crates.items()}, "libc": tree_hash(libc),
              "pthread": str(pthread.name), "binary": str(binary), "sha256": sha256_file(binary),
              "size": binary.stat().st_size}
    (root / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


PACKAGE_VERSION = "25.07.1-aros1"


def cmd_package(args):
    """Packs the last build into dist/Helix-<version>-abiv11.zip (never replaced)."""
    cfg = config(["aros_gcc"])
    root = Path(cfg["build_root"])
    record = json.loads((root / "build.json").read_text())
    binary = Path(record["binary"])
    if sha256_file(binary) != record["sha256"]:
        raise Failure("the binary changed since build.json was written; build again")
    if git(WORK / "helix", "rev-parse", "HEAD").stdout.decode().strip() != record["helix_work_head"]:
        raise Failure("work/helix moved since the build; build again")
    dist = REPO / "dist"
    name = f"Helix-{PACKAGE_VERSION}-abiv11"
    archive = dist / f"{name}.zip"
    if archive.exists():
        raise Failure(f"{archive} exists; delivered archives are never replaced")
    with tempfile.TemporaryDirectory(prefix="hx-package-") as tmp:
        top = Path(tmp) / "Helix"
        (top / "runtime").mkdir(parents=True)
        # The full strip leaves a file AROS loads without its relocations.
        run([cfg["aros_gcc"].replace("-gcc", "-strip"), "--strip-unneeded", "--remove-section", ".comment",
             "-o", top / "hx", binary])
        # UnZip restores these bits on AROS; without x the file has no `e` bit.
        (top / "hx").chmod(0o755)
        runtime = WORK / "helix" / "runtime"
        for name_ in GRAMMARS:
            shutil.copytree(runtime / "queries" / name_, top / "runtime/queries" / name_)
        shutil.copytree(runtime / "themes", top / "runtime/themes")
        shutil.copyfile(runtime / "tutor", top / "runtime/tutor")
        shutil.copyfile(REPO / "packaging/ReadMe.txt", top / "ReadMe.txt")
        shutil.copyfile(WORK / "helix" / "LICENSE", top / "LICENSE.txt")
        shutil.copyfile(root / "THIRD-PARTY.txt", top / "THIRD-PARTY.txt")
        contents = sorted(p for p in top.rglob("*") if p.is_file())
        (Path(tmp) / "contents.sha256").write_text("".join(
            f"{sha256_file(p)}  {p.relative_to(tmp).as_posix()}\n" for p in contents))
        dist.mkdir(exist_ok=True)
        run(["zip", "-qrX", archive, "Helix"], cwd=tmp)
        shutil.copyfile(Path(tmp) / "contents.sha256", dist / f"{name}.contents.sha256")
    with open(dist / "SHA256SUMS", "a") as sums:
        sums.write(f"{sha256_file(archive)}  {archive.name}\n")
    print(archive)


def cmd_config(args):
    cfg = config()
    for key, (env, _, description) in SETTINGS.items():
        print(f"{key:18} {cfg.get(key) or '(unset)':60} {env}: {description}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in (("config", cmd_config), ("materialize", cmd_materialize), ("export", cmd_export),
                     ("verify", cmd_verify), ("build", cmd_build), ("package", cmd_package)):
        sp = sub.add_parser(name, help=fn.__doc__)
        sp.set_defaults(fn=fn)
        if name == "build":
            # Unwind: a panic on the editor thread can then end the program from
            # the main task; AROS cannot abort() from a thread cleanly.
            sp.add_argument("--panic", choices=("unwind", "abort"), default="unwind")
            sp.add_argument("--probe", help="build experiments/<PROBE> instead of hx")
    args = p.parse_args()
    try:
        args.fn(args)
    except Failure as error:
        sys.exit(f"hx.py: {error}")


if __name__ == "__main__":
    main()
