#!/usr/bin/env python3
"""Helix for AROS: prepare patched sources and build for AROS x86_64 (ABIv11 and ABIv1).

Commands (run `hx.py COMMAND --help`):
  config       show the resolved settings
  materialize  create work/helix from the pin and patches/helix/series
  export       write the work copies' commits to patches/ (helix and crates)
  verify       rebuild every series in a temporary directory and compare trees
  build        build hx from the work copy into the build root
  package      pack the last build as a candidate in dist/unreleased
  promote      copy an accepted candidate unchanged into dist/

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
# (upstreams.json id, directory of the grammar in that repository). The revision
# must be the one Helix's languages.toml names.
GRAMMARS = {"rust": ("tree-sitter-rust", ""), "wat": ("tree-sitter-wasm", "wat")}

# Build targets (names as in AROS-dev coding standards). Per target:
#   abi_byte    e_ident[EI_ABIVERSION] the AROS linker writes for that ABI
#   spec        rust-aros file the target JSON is made from
#   pthread     "patched": link patches/aros-pthread objects ahead of the SDK's
#               libpthread.a (old ABIv11 SDK); "sdk": the SDK's library as is
#   wctype_shim the SDK lacks iswalnum and friends (ABIv11); mainline has them inline
#   panic       default panic strategy
#   unwind_fix  rebuild libgcc's unwind-dw2.o without the guard that AROS's GCC 10.5
#               patch had before AROS 52bab89955 (2026-09-13), when the toolchain's
#               object lacks it (see build_unwind_fix)
TARGETS = {
    "aros-x86_64-abiv11-pc": {"abi_byte": 11, "spec": "x86_64-aros.json.in", "pthread": "patched",
                              "wctype_shim": True, "panic": "unwind", "unwind_fix": False},
    "aros-x86_64-abiv1-pc": {"abi_byte": 1, "spec": "profiles/x86_64-v1-pc.json", "pthread": "sdk",
                             "wctype_shim": False, "panic": "unwind", "unwind_fix": True},
}
DEFAULT_TARGET = "aros-x86_64-abiv11-pc"

# key: (environment variable, default, description)
SETTINGS = {
    "aros_gcc": ("HX_AROS_GCC", None, "x86_64 AROS C compiler driver matching the SDK"),
    "aros_ar": ("HX_AROS_AR", None, "archiver from the same toolchain"),
    "aros_sdk": ("HX_AROS_SDK", None, "SDK of the target ABI, used as --sysroot"),
    "aros_src": ("HX_AROS_SRC", None, "deadwood2/AROS checkout; read-only source of the pthread link library"),
    "aros_src_commit": ("HX_AROS_SRC_COMMIT", "5376f09bc1ed0c36a12fbb096406013f8a046e89",
                        "commit whose compiler/pthread matches the SDK's libpthread.a"),
    "rust_aros": ("HX_RUST_AROS", None, "rust-aros Git checkout (target template, libc-aros, libc-dual.py)"),
    "rust_aros_commit": ("HX_RUST_AROS_COMMIT", None, "rust-aros commit to build from"),
    "toolchain": ("HX_TOOLCHAIN", None, "rustup name of the private AROS std toolchain"),
    "rustc_version": ("HX_RUSTC_VERSION", "rustc 1.100.0-nightly (0dfb098f3 2026-08-31)",
                      "exact `rustc --version` the toolchain must report"),
    "cargo_home": ("HX_CARGO_HOME", str(Path.home() / ".cargo"), "CARGO_HOME holding the registry cache"),
    "gcc_build": ("HX_GCC_BUILD", None, "host build directory of the AROS GCC that made aros_gcc "
                  "(<AROS build>/bin/<host>); read-only, for the unwinder fix (ABIv1)"),
    "build_root": ("HX_BUILD_ROOT", None, "build outputs (relative: to the repository); default build/<target>"),
}
# local.json may hold {"targets": {"<target>": {<settings>}}}: those values
# replace the top-level ones for that target. Environment variables still win.



class Failure(Exception):
    pass


def config(required=(), target=DEFAULT_TARGET):
    if target not in TARGETS:
        raise Failure(f"unknown target {target}; known: {', '.join(TARGETS)}")
    path = Path(os.environ.get("HX_LOCAL", REPO / "local.json"))
    local = json.loads(path.read_text()) if path.exists() else {}
    per_target = local.pop("targets", {})
    unknown = set(local) - set(SETTINGS)
    unknown |= {f"targets.{t}" for t in per_target if t not in TARGETS}
    unknown |= {f"targets.{t}.{k}" for t, v in per_target.items() for k in v if k not in SETTINGS}
    if unknown:
        raise Failure(f"{path}: unknown keys {sorted(unknown)}")
    local.update(per_target.get(target, {}))
    out = {"target": target}
    for key, (env, default, _) in SETTINGS.items():
        value = os.environ.get(env) or local.get(key) or default
        if key == "build_root":
            value = value or f"build/{target}"
            if not Path(value).is_absolute():
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


def build_unwind_fix(cfg, root):
    """libgcc's unwind-dw2.o with dwarf_reg_size_table filled in. AROS's GCC 10.5 patch
    put __builtin_init_dwarf_reg_size_table() under MD_FALLBACK_FRAME_STATE_FOR until
    AROS 52bab89955 (2026-09-13); toolchains built before it leave the table zeroed, and
    the first unwind (a Rust panic, a C++ throw, _Unwind_Backtrace) aborts in
    uw_init_context. Returns None when the toolchain's own object already has the table.
    Otherwise compiles the GCC build tree's unwind-dw2.c twice with the libgcc recipe:
    unchanged, whose disassembly and relocations (not its bytes) must match the
    toolchain's object, and without the guard, which is the object linked ahead of libgcc. Same fix as aros-svg-icons
    scripts/build-unwind-fix-v1.sh."""
    gcc, ar = cfg["aros_gcc"], cfg["aros_ar"]
    nm, objdump = gcc.replace("-gcc", "-nm"), gcc.replace("-gcc", "-objdump")
    libgcc = run([gcc, "-print-libgcc-file-name"]).stdout.decode().strip()

    def disassembly(obj):
        text = run([objdump, "-d", "-r", "--no-show-raw-insn", obj]).stdout
        return hashlib.sha256(text.decode().split("\n", 4)[-1].encode()).hexdigest()

    with tempfile.TemporaryDirectory(prefix="unwind-", dir=root) as tmp:
        tmp = Path(tmp)
        run([ar, "x", libgcc, "unwind-dw2.o"], cwd=tmp)
        stock = tmp / "unwind-dw2.o"
        if " dwarf_reg_size_table\n" in run([nm, stock]).stdout.decode():
            return None
        if not cfg.get("gcc_build"):
            raise Failure(f"{libgcc}'s unwinder never fills its register size table, so every "
                          "panic aborts; set gcc_build (HX_GCC_BUILD) to that toolchain's GCC build tree")
        build = Path(cfg["gcc_build"])
        g = build / "gen/host/tools/crosstools/gnu/gcc"
        source = build / "Ports/host/gcc/gcc-10.5.0/libgcc/unwind-dw2.c"
        toolchain = Path(gcc).parent
        inputs = {"source": sha256_file(source), "stock": disassembly(stock), "xgcc": sha256_file(g / "gcc/xgcc"),
                  "sdk": cfg["aros_sdk"]}
        key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
        out = root / f"unwind-fix-{key}"
        if (out / "unwind.json").exists():
            record = json.loads((out / "unwind.json").read_text())
            if record["inputs"] != inputs or sha256_file(out / "unwind-dw2.o") != record["object"]:
                raise Failure(f"changed unwinder cache {out}; keep it for inspection, use a new build root")
            return out / "unwind-dw2.o"
        text = source.read_text()
        guard = "#ifdef MD_FALLBACK_FRAME_STATE_FOR\n  __builtin_init_dwarf_reg_size_table (dwarf_reg_size_table);\n#endif\n"
        if text.count(guard) != 1:
            raise Failure(f"{source}: the guarded table initialisation is not there once")
        (tmp / "fixed.c").write_text(text.replace(guard, "  __builtin_init_dwarf_reg_size_table (dwarf_reg_size_table);\n"))
        # The recipe of the toolchain's libgcc build (its libgcc build directory as cwd).
        command = [g / "gcc/xgcc", f"-B{g}/gcc/", f"-B{toolchain}/x86_64-aros/bin/", f"-B{toolchain}/x86_64-aros/lib/",
                   f"--sysroot={cfg['aros_sdk']}", "-isystem", f"{cfg['aros_sdk']}/include",
                   "-mcmodel=large", "-O2", "-DIN_GCC", "-DCROSS_DIRECTORY_STRUCTURE", "-isystem", "./include",
                   "-DIN_LIBGCC2", "-fbuilding-libgcc", "-fno-stack-protector", "-I.", "-I../.././gcc",
                   f"-I{source.parent}", f"-I{source.parent}/../gcc", f"-I{source.parent}/../include",
                   "-fexceptions", "-fvisibility=hidden", "-DHIDE_EXPORTS", "-c"]
        cwd = g / "x86_64-aros/libgcc"
        run(command + [source, "-o", tmp / "rebuilt.o"], cwd=cwd)
        if disassembly(tmp / "rebuilt.o") != inputs["stock"]:
            raise Failure(f"unwind-dw2.c from {build} does not rebuild {libgcc}'s object; wrong gcc_build?")
        run(command + [tmp / "fixed.c", "-o", tmp / "unwind-dw2.o.fixed"], cwd=cwd)
        if " dwarf_reg_size_table\n" not in run([nm, tmp / "unwind-dw2.o.fixed"]).stdout.decode():
            raise Failure("the rebuilt unwinder still has no register size table")
        staging = Path(tempfile.mkdtemp(prefix="unwind-fix-", dir=root))
        shutil.copyfile(tmp / "unwind-dw2.o.fixed", staging / "unwind-dw2.o")
        (staging / "unwind.json").write_text(json.dumps(
            {"inputs": inputs, "object": sha256_file(staging / "unwind-dw2.o")}, indent=2) + "\n")
        staging.rename(out)
    return out / "unwind-dw2.o"


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
    """Compiles each linked grammar (parser.c, scanner.c) into one archive, with
    the wctype fallback its scanner needs where the SDK lacks those functions."""
    repos = json.loads(UPSTREAMS.read_text())["repositories"]
    out = root / "grammars"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shim = REPO / "src/grammars"
    flags = [f"--sysroot={cfg['aros_sdk']}", "-O2", "-fno-PIC", "-std=c11", "-w"]
    shimmed = TARGETS[cfg["target"]]["wctype_shim"]
    objects, include = [], []
    if shimmed:
        objects.append(out / "wctype_aros.o")
        run([cfg["aros_gcc"], *flags, "-c", shim / "wctype_aros.c", "-o", objects[0]])
        include = ["-include", shim / "wctype_aros.h"]
    for name, (ident, subdir) in GRAMMARS.items():
        checkout = REPO / "upstream" / ident
        commit = repos[ident]["commit"]
        if git(checkout, "rev-parse", "HEAD").stdout.decode().strip() != commit:
            raise Failure(f"{checkout} is not at the pinned {commit}")
        if git(checkout, "status", "--porcelain").stdout.strip():
            raise Failure(f"{checkout} has local changes; it must stay clean")
        src = checkout / subdir / "src"
        for part in ("parser", "scanner"):
            if (src / f"{part}.c").exists():
                obj = out / f"{name}-{part}.o"
                run([cfg["aros_gcc"], *flags, *include, "-I", src,
                     "-c", src / f"{part}.c", "-o", obj])
                objects.append(obj)
    archive = out / "libhelix-grammars.a"
    run([cfg["aros_ar"], "rcs", archive, *objects])
    return archive


def build_launcher(cfg, root):
    """Compiles src/launcher/hx.c, the program that starts hx-editor with the stack it needs."""
    out = root / "launcher"
    out.mkdir(parents=True, exist_ok=True)
    binary = out / "hx"
    run([cfg["aros_gcc"], f"--sysroot={cfg['aros_sdk']}", "-O2", "-Wall", "-Wextra", "-Werror",
         "-Werror=implicit-function-declaration", f"-ffile-prefix-map={REPO}=/aros-helix",
         "-o", binary, REPO / "src/launcher/hx.c"])
    return binary


def target_spec(cfg, rtree):
    """The target JSON for cfg's target, from the rust-aros file at the pinned commit."""
    info = TARGETS[cfg["target"]]
    if info["spec"].endswith(".in"):
        return ((rtree / info["spec"]).read_text().replace("@AROS_GCC@", cfg["aros_gcc"])
                .replace("@AROS_SDK@", cfg["aros_sdk"]).replace("@AROS_EXTRA_LIBS@", ""))
    # rust-aros profiles carry no machine paths: add the linker and SDK. libpthread
    # comes last, after libc-aros's objects that call it.
    spec = json.loads((rtree / info["spec"]).read_text())
    spec["linker"] = cfg["aros_gcc"]
    spec["pre-link-args"] = {"gnu-cc": [f"--sysroot={cfg['aros_sdk']}"]}
    spec["late-link-args"] = {"gnu-cc": ["-lpthread"]}
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


# Layouts libc-aros declares that must match the SDK's C headers. Rust name ->
# C type, for size and alignment; then (Rust type, Rust field, C type, C member)
# for offsets std and Helix's crates read. pollfd, ip_mreq and ipv6_mreq are left
# out: the ABIv11 SDK headers do not declare them.
ABI_TYPES = {
    "pthread_attr_t": "pthread_attr_t", "pthread_mutexattr_t": "pthread_mutexattr_t",
    "pthread_mutex_t": "pthread_mutex_t", "pthread_condattr_t": "pthread_condattr_t",
    "pthread_cond_t": "pthread_cond_t", "pthread_rwlock_t": "pthread_rwlock_t",
    "pthread_t": "pthread_t", "pthread_key_t": "pthread_key_t",
    "stat": "struct stat", "dirent": "struct dirent", "timespec": "struct timespec",
    "timeval": "struct timeval", "tm": "struct tm", "termios": "struct termios",
    "winsize": "struct winsize", "sigset_t": "sigset_t", "sigaction": "struct sigaction",
    "passwd": "struct passwd", "addrinfo": "struct addrinfo", "linger": "struct linger",
    "sockaddr": "struct sockaddr", "sockaddr_in": "struct sockaddr_in", "sockaddr_in6": "struct sockaddr_in6",
    "sockaddr_un": "struct sockaddr_un", "sockaddr_storage": "struct sockaddr_storage",
    "in_addr": "struct in_addr", "in6_addr": "struct in6_addr", "iovec": "struct iovec",
    "off_t": "off_t", "time_t": "time_t", "ino_t": "ino_t", "mode_t": "mode_t", "nlink_t": "nlink_t",
    "pid_t": "pid_t", "dev_t": "dev_t", "uid_t": "uid_t", "gid_t": "gid_t", "blksize_t": "blksize_t",
    "blkcnt_t": "blkcnt_t", "clockid_t": "clockid_t", "suseconds_t": "suseconds_t",
    "socklen_t": "socklen_t", "sa_family_t": "sa_family_t", "cc_t": "cc_t", "tcflag_t": "tcflag_t",
    "speed_t": "speed_t",
}
ABI_FIELDS = [
    ("stat", "st_ino", "struct stat", "st_ino"), ("stat", "st_mode", "struct stat", "st_mode"),
    ("stat", "st_nlink", "struct stat", "st_nlink"), ("stat", "st_uid", "struct stat", "st_uid"),
    ("stat", "st_gid", "struct stat", "st_gid"), ("stat", "st_rdev", "struct stat", "st_rdev"),
    ("stat", "st_size", "struct stat", "st_size"), ("stat", "st_atime", "struct stat", "st_atim.tv_sec"),
    ("stat", "st_atime_nsec", "struct stat", "st_atim.tv_nsec"), ("stat", "st_mtime", "struct stat", "st_mtim.tv_sec"),
    ("stat", "st_mtime_nsec", "struct stat", "st_mtim.tv_nsec"), ("stat", "st_ctime", "struct stat", "st_ctim.tv_sec"),
    ("stat", "st_ctime_nsec", "struct stat", "st_ctim.tv_nsec"), ("stat", "st_blksize", "struct stat", "st_blksize"),
    ("stat", "st_blocks", "struct stat", "st_blocks"),
    ("dirent", "d_off", "struct dirent", "d_off"), ("dirent", "d_reclen", "struct dirent", "d_reclen"),
    ("dirent", "d_type", "struct dirent", "d_type"), ("dirent", "d_name", "struct dirent", "d_name"),
    ("timespec", "tv_nsec", "struct timespec", "tv_nsec"), ("timeval", "tv_usec", "struct timeval", "tv_usec"),
    ("tm", "tm_gmtoff", "struct tm", "tm_gmtoff"), ("tm", "tm_zone", "struct tm", "tm_zone"),
    ("termios", "c_cc", "struct termios", "c_cc"), ("winsize", "ws_col", "struct winsize", "ws_col"),
    ("sigaction", "sa_mask", "struct sigaction", "sa_mask"), ("sigaction", "sa_flags", "struct sigaction", "sa_flags"),
    ("passwd", "pw_uid", "struct passwd", "pw_uid"), ("passwd", "pw_dir", "struct passwd", "pw_dir"),
    ("passwd", "pw_passwd", "struct passwd", "pw_passwd"),
    ("addrinfo", "ai_addrlen", "struct addrinfo", "ai_addrlen"), ("addrinfo", "ai_canonname", "struct addrinfo", "ai_canonname"),
    ("addrinfo", "ai_addr", "struct addrinfo", "ai_addr"), ("addrinfo", "ai_next", "struct addrinfo", "ai_next"),
    ("linger", "l_linger", "struct linger", "l_linger"),
    ("sockaddr_in", "sin_addr", "struct sockaddr_in", "sin_addr"),
    ("sockaddr_in6", "sin6_addr", "struct sockaddr_in6", "sin6_addr"),
    ("sockaddr_in6", "sin6_scope_id", "struct sockaddr_in6", "sin6_scope_id"),
]
ABI_HEADERS = ("sys/types.h", "sys/stat.h", "sys/time.h", "sys/socket.h", "sys/uio.h", "sys/un.h", "sys/ioctl.h",
               "netinet/in.h", "netdb.h", "dirent.h", "pwd.h", "termios.h", "signal.h", "pthread.h", "time.h")


def abi_gate(cfg, root, libc, target_json):
    """Fails the build when libc-aros disagrees with the SDK about a layout in ABI_TYPES
    or ABI_FIELDS. The SDK side comes from its own compiler (array sizes in an object
    file, read with nm); the Rust side from const assertions in a `cargo check`.
    Nothing runs on AROS."""
    out = root / "abi-gate"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    probes = [(f"s{i}", f"sizeof({c})", None) for i, c in enumerate(ABI_TYPES.values())]
    probes += [(f"a{i}", f"_Alignof({c})", None) for i, c in enumerate(ABI_TYPES.values())]
    # A zero-length array is not allowed: offsets are stored plus one.
    probes += [(f"o{i}", f"offsetof({ct}, {cm}) + 1", 1) for i, (_, _, ct, cm) in enumerate(ABI_FIELDS)]
    source = "".join(f"#include <{h}>\n" for h in ("stddef.h",) + ABI_HEADERS)
    source += "".join(f"unsigned char abi_{n}[{e}] = {{1}};\n" for n, e, _ in probes)
    (out / "layout.c").write_text(source)
    run([cfg["aros_gcc"], f"--sysroot={cfg['aros_sdk']}", "-c", out / "layout.c", "-o", out / "layout.o"])
    sizes = {}
    for line in run([cfg["aros_gcc"].replace("-gcc", "-nm"), "-S", "--defined-only", out / "layout.o"]).stdout.decode().splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3].startswith("abi_"):
            sizes[parts[3][4:]] = int(parts[1], 16)
    measured = {n: sizes[n] - (bias or 0) for n, _, bias in probes}
    checks = []
    for i, rust in enumerate(ABI_TYPES):
        checks.append((f"size_of::<libc::{rust}>()", measured[f"s{i}"], f"size of {rust}"))
        checks.append((f"align_of::<libc::{rust}>()", measured[f"a{i}"], f"alignment of {rust}"))
    for i, (rt, rf, _, cm) in enumerate(ABI_FIELDS):
        checks.append((f"offset_of!(libc::{rt}, {rf})", measured[f"o{i}"], f"offset of {rt}.{rf} (C {cm})"))
    lib = ["#![no_std]", "use core::mem::{align_of, offset_of, size_of};"]
    lib += [f'const _: () = assert!({expr} == {value}, "{what}: the SDK has {value}");' for expr, value, what in checks]
    (out / "lib.rs").write_text("\n".join(lib) + "\n")
    (out / "Cargo.toml").write_text(
        '[package]\nname = "abi-gate"\nversion = "0.0.0"\nedition = "2021"\npublish = false\n\n'
        '[lib]\npath = "lib.rs"\n\n[dependencies]\nlibc = { path = ' + json.dumps(str(libc)) + ' }\n\n[workspace]\n')
    (out / "sdk-layout.json").write_text(json.dumps({what: value for _, value, what in checks}, indent=1) + "\n")
    env = dict(os.environ, CARGO_HOME=cfg["cargo_home"], CARGO_TARGET_DIR=str(root / "abi-gate-target"),
               AROS_GCC=cfg["aros_gcc"], AROS_SDK=cfg["aros_sdk"], AROS_AR=cfg["aros_ar"])
    result = run(["cargo", "+" + cfg["toolchain"], "check", "--offline", "--target", target_json,
                  "-Zbuild-std=core", "-Zjson-target-spec"], cwd=out, env=env, check=False)
    if result.returncode:
        log = result.stderr.decode()
        wrong = sorted(set(re.findall(r'"([^"]+: the SDK has \d+)"', log)))
        raise Failure("libc-aros does not match the SDK's C layouts:\n  " + "\n  ".join(wrong or [log[-3000:]]))
    print(f"abi gate: {len(checks)} layouts of libc-aros match {cfg['aros_sdk']}")
    return sha256_file(out / "sdk-layout.json")


def cmd_build(args):
    info = TARGETS.get(args.target)
    needs = ["aros_gcc", "aros_ar", "aros_sdk", "rust_aros", "rust_aros_commit", "toolchain"]
    if info and info["pthread"] == "patched":
        needs.append("aros_src")
    cfg = config(needs, args.target)
    panic = args.panic or info["panic"]
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
                       "std-groundwork/libc-aros", "tools/libc-dual.py", info["spec"]]).stdout
        with tempfile.TemporaryDirectory(prefix="rust-aros-", dir=root) as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tmp, filter="data")
            Path(tmp).rename(rtree)
    libc = root / f"libc-dual-{rcommit[:12]}"
    if not libc.exists():
        run([sys.executable, rtree / "tools/libc-dual.py", rtree / "std-groundwork/libc-aros", libc,
             "--cargo-home", cfg["cargo_home"]])
    target_json = root / "x86_64-aros.json"
    text = target_spec(cfg, rtree)
    if not target_json.exists() or target_json.read_text() != text:
        target_json.write_text(text)
    # --no-abi-gate only for diagnostic rebuilds of an earlier input set; package
    # refuses such a build.
    sdk_layout = "skipped" if args.no_abi_gate else abi_gate(cfg, root, libc, target_json)

    crates = {}
    for spec in crate_specs():
        ident = f"{spec['name']}-{spec['version']}"
        dest = root / "crates" / ident
        stage_tree(WORK / "crates" / ident, dest)
        crates[spec["name"]] = dest
    pthread = build_pthread(cfg, root) if info["pthread"] == "patched" else None
    unwind_fix = build_unwind_fix(cfg, root) if info["unwind_fix"] else None
    grammars = build_grammars(cfg, root)
    launcher = build_launcher(cfg, root)

    sysroot = run(["rustc", "+" + cfg["toolchain"], "--print", "sysroot"]).stdout.decode().strip()
    # getrandom 0.3 has no AROS backend: helix-stdx supplies the custom one.
    flags = ["-Cpanic=" + panic, "--cfg", 'getrandom_backend="custom"', "--cfg", "tokio_unstable",
             f"--remap-path-prefix={src}=/helix", f"--remap-path-prefix={cfg['cargo_home']}=/cargo",
             f"--remap-path-prefix={libc}=/libc-aros", f"--remap-path-prefix={sysroot}=/rust",
             # Patched crates are staged under the build root; without this their
             # host paths end up in panic locations.
             f"--remap-path-prefix={root / 'crates'}=/crates",
             # One section per function gives hx about 12 000 loadable sections, and
             # AROS keeps them as that many segments. ABIv11 debug.library lists a
             # loaded module before building its segment array; a task that ends
             # meanwhile (UnLoadSeg -> UnregisterModule) reads the missing array and
             # fails. Fewer sections make that window and every module walk shorter.
             "-Zfunction-sections=no"]
    # The AROS GCC driver searches the SDK's lib/ before any -L, so a patched
    # libpthread.a cannot win the -lpthread lookup. The two patched objects go first
    # on the link line instead: they define every symbol of the SDK's pthread.o and
    # pthread_create.o, so -lpthread pulls neither.
    if panic == "unwind":
        flags.append("-Cforce-unwind-tables=yes")
    # Objects linked ahead of the libraries: they define every symbol of the archive
    # member they replace, so that member is never pulled.
    pre_link = [str(pthread / f"{n}.o") for n in PTHREAD_PATCHED] if pthread else []
    if unwind_fix:
        pre_link.append(str(unwind_fix))
    if pre_link:
        flags.append("-Zpre-link-args=" + " ".join(pre_link))
    if args.lsp_experimental:
        # Experimental LSP variant: language servers start, nothing else does
        # (helix-stdx/src/process.rs on the experimental branch).
        flags += ["--cfg", "aros_lsp_cooperative"]
    if args.probe_flag:
        if not args.probe:
            raise Failure("--probe-flag applies to --probe builds only")
        flags += args.probe_flag
    if not args.probe:
        # helix-loader refers to tree_sitter_<name> for each grammar in GRAMMARS.
        flags.append(f"-Clink-arg={grammars}")
    patch = ["--config", "patch.crates-io.libc.path=" + json.dumps(str(libc))]
    for name, path in crates.items():
        patch += ["--config", f"patch.crates-io.{name}.path=" + json.dumps(str(path))]
    env = dict(os.environ, AROS_GCC=cfg["aros_gcc"], AROS_SDK=cfg["aros_sdk"], AROS_AR=cfg["aros_ar"],
               CARGO_HOME=cfg["cargo_home"], CARGO_TARGET_DIR=str(root / "target"),
               HELIX_DISABLE_AUTO_GRAMMAR_BUILD="1",
               # Helix's build script names the Git commit it finds; the staged
               # copy sits inside this repository, whose commit is not Helix's.
               GIT_CEILING_DIRECTORIES=str(root),
               # The C parts (tree-sitter's runtime) for the cc crate.
               CC_x86_64_aros=cfg["aros_gcc"], AR_x86_64_aros=cfg["aros_ar"],
               # The cc crate asks for PIC; AROS executables are static, and PIC
               # objects reference a _GLOBAL_OFFSET_TABLE_ the link never has.
               # -ffile-prefix-map: C assertions name their source file; no host path.
               CFLAGS_x86_64_aros=f"--sysroot={cfg['aros_sdk']} -fno-PIC -ffile-prefix-map={root}=/build "
                                  f"-ffile-prefix-map={cfg['cargo_home']}=/cargo")
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
                           "-Zbuild-std=std,panic_" + panic, "-Zjson-target-spec",
                           "--config", "build.rustflags=" + json.dumps(flags)] + patch
        result = run(command, cwd=probe_src, env=dict(env, CARGO_TARGET_DIR=str(root / "probe-target")),
                     capture=False, check=False)
        if result.returncode:
            raise Failure(f"cargo build failed ({result.returncode})")
        print(root / "probe-target/x86_64-aros/release" / args.probe)
        return
    command = cargo + ["build", "--release", "--offline", "-p", "helix-term", "--no-default-features",
                       "--target", target_json, "-Zbuild-std=std,panic_" + panic, "-Zjson-target-spec",
                       "--config", "build.rustflags=" + json.dumps(flags)] + patch
    result = run(command, cwd=src, env=env, capture=False, check=False)
    if result.returncode:
        raise Failure(f"cargo build failed ({result.returncode})")
    binary = root / "target/x86_64-aros/release/hx"
    abi_byte = binary.read_bytes()[8]
    if abi_byte != info["abi_byte"]:
        raise Failure(f"{binary} has ELF ABI version {abi_byte}, {args.target} needs {info['abi_byte']}")
    # Licences of everything compiled into hx, for the archive.
    tree = run(cargo + ["tree", "--offline", "-p", "helix-term", "--no-default-features", "-e", "normal",
                        "--prefix", "none", "--format", "{p}: {l}", "--target", target_json,
                        "-Zjson-target-spec"] + patch, cwd=src, env=env).stdout.decode()
    # Drop local paths and "(*)" marks: "helix-core v25.7.1 (/...): MPL-2.0".
    lines = sorted({re.sub(r" \([^)]*\)", "", line) for line in tree.splitlines() if line.strip()})
    for name, (ident, _) in GRAMMARS.items():
        repo = json.loads(UPSTREAMS.read_text())["repositories"][ident]
        lines.append(f"{ident} {repo['commit'][:12]}: {repo['license']}")
    (root / "THIRD-PARTY.txt").write_text(
        "Helix for AROS includes these components, under the licences their authors chose.\n"
        "Their full texts are in each component's source.\n\n" + "\n".join(lines) + "\n")
    sdk = Path(cfg["aros_sdk"])
    record = {"target": args.target, "helix_work_head": head, "rust_aros_commit": rcommit,
              "toolchain": cfg["toolchain"], "rustc": rustc, "rust_src": tree_hash(Path(sysroot) / "lib/rustlib/src/rust/library"),
              "panic": panic, "function_sections": "no", "gcc": run([cfg["aros_gcc"], "--version"]).stdout.decode().splitlines()[0],
              "sdk_libraries": {n: sha256_file(sdk / "lib" / n) for n in ("libpthread.a", "libposixc.a", "libstdc.a")
                                if (sdk / "lib" / n).exists()},
              "crates": {n: tree_hash(p) for n, p in crates.items()}, "libc": tree_hash(libc),
              "pthread": pthread.name if pthread else "SDK libpthread.a",
              "unwinder": sha256_file(unwind_fix) if unwind_fix else "toolchain libgcc", "grammars": sha256_file(grammars),
              "target_json": sha256_file(target_json), "sdk_layout": sdk_layout,
              "variant": "lsp-experimental" if args.lsp_experimental else "default",
              "launcher": str(launcher), "launcher_sha256": sha256_file(launcher),
              "binary": str(binary), "sha256": sha256_file(binary), "size": binary.stat().st_size}
    (root / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


PACKAGE_VERSION = "25.07.1-preview2"

# Words the ReadMe template fills in per target.
README_FIELDS = {
    "aros-x86_64-abiv11-pc": {"@SYSTEM@": "AROS x86_64 ABIv11 (AROS One and other ABIv11 systems); tested on AROS One 1.3",
                              "@LIMPET@": "Limpet-0.9.0-aros-x86_64-abiv11-pc.zip"},
    "aros-x86_64-abiv1-pc": {"@SYSTEM@": "mainline AROS x86_64 (ABIv1); tested on the nightly build of 2026-10-31",
                             "@LIMPET@": "Limpet-0.9.0-aros-x86_64-abiv1-pc.zip"},
}


def cmd_package(args):
    """Packs the last build as candidate N: dist/unreleased/Helix-<version>-candidate<N>-<target>.zip.
    --unstripped packs the same linked binary without stripping it (for diagnosis; never
    promoted). Only `promote` puts an archive into dist/ and its SHA256SUMS. Nothing is replaced."""
    cfg = config(["aros_gcc"], args.target)
    root = Path(cfg["build_root"])
    record = json.loads((root / "build.json").read_text())
    if record.get("target") != args.target:
        raise Failure(f"{root}/build.json is for {record.get('target')}, not {args.target}")
    binary = Path(record["binary"])
    if sha256_file(binary) != record["sha256"]:
        raise Failure("the binary changed since build.json was written; build again")
    if git(WORK / "helix", "rev-parse", "HEAD").stdout.decode().strip() != record["helix_work_head"]:
        raise Failure("work/helix moved since the build; build again")
    if record.get("sdk_layout", "skipped") == "skipped":
        raise Failure("this build did not pass the ABI layout gate; it is for diagnosis only")
    lsp = record.get("variant", "default") == "lsp-experimental"
    if lsp != bool(args.lsp_experimental):
        raise Failure(f"the last build is the {record.get('variant', 'default')} variant; "
                      "--lsp-experimental must match it")
    if lsp and not args.server:
        raise Failure("--lsp-experimental needs --server (the language server build record, JSON)")
    if (args.iteration is None) == (args.candidate is None):
        raise Failure("give either --candidate N or --iteration N")
    if args.iteration is not None:
        if not lsp or args.unstripped:
            raise Failure("--iteration packs the LSP build (--lsp-experimental), stripped")
        package_iteration(args, cfg, root, record, binary)
        return
    dist = REPO / "dist" / "unreleased"
    variant = "-unstripped" if args.unstripped else ""
    kind = "lsp-experimental" if lsp else "candidate"
    name = f"Helix-{PACKAGE_VERSION}-{kind}{args.candidate}{variant}-{args.target}"
    archive = dist / f"{name}.zip"
    if archive.exists():
        raise Failure(f"{archive} exists; delivered archives are never replaced")
    with tempfile.TemporaryDirectory(prefix="hx-package-") as tmp:
        top = Path(tmp) / ("Helix-LSP" if lsp else "Helix")
        (top / "runtime").mkdir(parents=True)
        if args.unstripped:
            # Diagnostic variant: the linked file itself, with its symbols.
            shutil.copyfile(binary, top / "hx")
        else:
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
        readme = (REPO / ("packaging/lsp-experimental/ReadMe.txt" if lsp else "packaging/ReadMe.txt")).read_text()
        for word, value in README_FIELDS[args.target].items():
            readme = readme.replace(word, value)
        left = re.findall(r"@[A-Z_]+@", readme)
        if left:
            raise Failure(f"packaging/ReadMe.txt: no value for {left}")
        (top / "ReadMe.txt").write_text(readme)
        shutil.copyfile(WORK / "helix" / "LICENSE", top / "LICENSE.txt")
        shutil.copyfile(root / "THIRD-PARTY.txt", top / "THIRD-PARTY.txt")
        if lsp:
            add_language_server(args.server, top)
        contents = sorted(p for p in top.rglob("*") if p.is_file())
        (Path(tmp) / "contents.sha256").write_text("".join(
            f"{sha256_file(p)}  {p.relative_to(tmp).as_posix()}\n" for p in contents))
        dist.mkdir(exist_ok=True)
        run(["zip", "-qrX", archive, top.name], cwd=tmp)
        shutil.copyfile(Path(tmp) / "contents.sha256", dist / f"{name}.contents.sha256")
    with open(dist / f"SHA256SUMS-{PACKAGE_VERSION}", "a") as sums:
        sums.write(f"{sha256_file(archive)}  {archive.name}\n")
    print(archive)


# Archive suffix per target in the port's iteration names (Helix-25.07.1-aros1-abiv11.zip).
ITERATION_ABI = {"aros-x86_64-abiv11-pc": "abiv11", "aros-x86_64-abiv1-pc": "abiv1"}


def package_iteration(args, cfg, root, record, binary):
    """Packs port iteration N straight into dist/: Helix-25.07.1-aros<N>-<abi>.zip, its
    contents list, and a line in dist/SHA256SUMS. Being in dist/ is not a publication.
    The drawer `Helix` holds the launcher `hx`, the editor `hx-editor`, the language
    server `wat_server` (Helix's default command for .wat), runtime files for the linked
    grammars, the example, licences and the ReadMe. Nothing is replaced."""
    launcher = Path(record["launcher"])
    if sha256_file(launcher) != record["launcher_sha256"]:
        raise Failure("the launcher changed since build.json was written; build again")
    # --draft: the same archive under the build root, for tests before delivery.
    dist = root / "draft" if args.draft else REPO / "dist"
    dist.mkdir(exist_ok=True)
    name = f"Helix-25.07.1-aros{args.iteration}-{ITERATION_ABI[args.target]}"
    archive, listing, sums = dist / f"{name}.zip", dist / f"{name}.contents.sha256", dist / "SHA256SUMS"
    if archive.exists() or listing.exists() or (sums.exists() and f"  {archive.name}\n" in sums.read_text()):
        raise Failure(f"{archive} exists; delivered archives are never replaced")
    server = json.loads(Path(args.server).read_text())
    if sha256_file(server["binary"]) != server["sha256"]:
        raise Failure(f"{server['binary']} does not match the sha256 in {args.server}")
    extra = REPO / "packaging/iteration"
    with tempfile.TemporaryDirectory(prefix="hx-package-") as tmp:
        top = Path(tmp) / "Helix"
        (top / "runtime").mkdir(parents=True)
        # The full strip leaves a file AROS loads without its relocations.
        run([cfg["aros_gcc"].replace("-gcc", "-strip"), "--strip-unneeded", "--remove-section", ".comment",
             "-o", top / "hx-editor", binary])
        shutil.copyfile(launcher, top / "hx")
        shutil.copyfile(server["binary"], top / "wat_server")
        for exe in ("hx", "hx-editor", "wat_server"):
            # UnZip restores these bits on AROS; without x the file has no `e` bit.
            (top / exe).chmod(0o755)
        runtime = WORK / "helix" / "runtime"
        for grammar in GRAMMARS:
            shutil.copytree(runtime / "queries" / grammar, top / "runtime/queries" / grammar)
        shutil.copytree(runtime / "themes", top / "runtime/themes")
        shutil.copyfile(runtime / "tutor", top / "runtime/tutor")
        readme = (extra / "ReadMe.txt").read_text()
        for word, value in README_FIELDS[args.target].items():
            readme = readme.replace(word, value)
        left = re.findall(r"@[A-Z_]+@", readme)
        if left:
            raise Failure(f"packaging/iteration/ReadMe.txt: no value for {left}")
        (top / "ReadMe.txt").write_text(readme)
        shutil.copyfile(extra / "example.wat", top / "example.wat")
        shutil.copyfile(WORK / "helix" / "LICENSE", top / "LICENSE.txt")
        shutil.copyfile(root / "THIRD-PARTY.txt", top / "THIRD-PARTY.txt")
        shutil.copyfile(server["license"], top / "wat_server-LICENSE.txt")
        shutil.copyfile(server["third_party"], top / "wat_server-THIRD-PARTY.txt")
        # Licence texts of the grammars compiled into hx-editor.
        (top / "licenses").mkdir()
        for ident, _ in GRAMMARS.values():
            text = (REPO / "upstream" / ident / "LICENSE").read_text()
            # A LICENSE that is only an SPDX line gets the full text from packaging/licenses.
            spdx = re.fullmatch(r"SPDX-License-Identifier: (.+)\n?", text)
            if spdx:
                full = REPO / "packaging/licenses" / (spdx.group(1).replace(" ", "-") + ".txt")
                if not full.exists():
                    raise Failure(f"{ident}: no licence text {full} for {spdx.group(1)}")
                text += "\n" + full.read_text()
            (top / "licenses" / f"{ident}.txt").write_text(text)
        contents = sorted(p for p in top.rglob("*") if p.is_file())
        (Path(tmp) / "contents.sha256").write_text("".join(
            f"{sha256_file(p)}  {p.relative_to(tmp).as_posix()}\n" for p in contents))
        run(["zip", "-qrX", archive, top.name], cwd=tmp)
        shutil.copyfile(Path(tmp) / "contents.sha256", listing)
    with open(sums, "a") as out:
        out.write(f"{sha256_file(archive)}  {archive.name}\n")
    print(archive)


def add_language_server(record_path, top):
    """Copies the experimental package's language server and its files into `top`.
    The record (JSON) names: "binary" (an AROS executable) with its "sha256",
    "license" (the server's licence text) and "third_party" (its dependency list);
    packaging/lsp-experimental supplies the configuration, example and launcher."""
    record = json.loads(Path(record_path).read_text())
    binary = Path(record["binary"])
    if sha256_file(binary) != record["sha256"]:
        raise Failure(f"{binary} does not match the sha256 in {record_path}")
    shutil.copyfile(binary, top / "wat-server")
    (top / "wat-server").chmod(0o755)
    shutil.copyfile(record["license"], top / "wat-server-LICENSE.txt")
    shutil.copyfile(record["third_party"], top / "wat-server-THIRD-PARTY.txt")
    extra = REPO / "packaging/lsp-experimental"
    for name in ("languages.toml", "example.wat", "hx-lsp"):
        shutil.copyfile(extra / name, top / name)
    (top / "hx-lsp").chmod(0o755)


# Scenarios a native acceptance must pass before a candidate is promoted (both targets).
# LSP and forced stop of child programs are outside preview 2.
ACCEPTANCE_SCENARIOS = (
    "edit-save",          # typing, Unicode, spaces, :w, file bytes checked on the host
    "macros",             # record and replay
    "split",              # split windows, switching, closing one
    "refusals",           # :sh, :pipe and a formatter refuse without starting anything
    "protected-file",     # writing a protected file reports the error, the buffer stays
    "unsaved-quit",       # :q refuses with changes, :q! ends
    "resize",             # window resize redraws
    "window-close",       # closing the Limpet window ends hx
    "repeated-sessions",  # several hx sessions in one boot, documents on SYS: and in RAM:
    "clean-exit",         # hx and Limpet return 0, no process left, no system alert
)
ACCEPTANCE_ABI = {"aros-x86_64-abiv11-pc": "v11", "aros-x86_64-abiv1-pc": "v1"}


def check_acceptance(path, source, digest, target):
    """The acceptance record is JSON:
        {"schema": "helix-acceptance-1", "artifact": "<candidate zip name>", "sha256": "<its digest>",
         "target": "<target>", "outcome": "pass",
         "scenarios": {"<name>": {"outcome": "pass", "report": "<REPORT.json, relative to the record>"}}}
    Every scenario in ACCEPTANCE_SCENARIOS must pass, and each named run report
    (aros-vm-runs REPORT.json) must have passed on the target's ABI with this archive
    among its inputs. Anything else, including an unknown outcome, is a refusal."""
    path = Path(path)
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise Failure(f"{path}: not a readable acceptance record ({error})")
    if not isinstance(record, dict) or record.get("schema") != "helix-acceptance-1":
        raise Failure(f"{path}: schema is not helix-acceptance-1")
    for key, want in (("artifact", source.name), ("sha256", digest), ("target", target), ("outcome", "pass")):
        if record.get(key) != want:
            raise Failure(f"{path}: {key} is {record.get(key)!r}, expected {want!r}")
    scenarios = record.get("scenarios")
    if not isinstance(scenarios, dict):
        raise Failure(f"{path}: no scenarios")
    missing = [s for s in ACCEPTANCE_SCENARIOS if s not in scenarios]
    if missing:
        raise Failure(f"{path}: scenarios missing: {', '.join(missing)}")
    for name in ACCEPTANCE_SCENARIOS:
        entry = scenarios[name]
        if not isinstance(entry, dict) or entry.get("outcome") != "pass":
            raise Failure(f"{path}: scenario {name} did not pass")
        report_path = path.parent / str(entry.get("report", ""))
        try:
            report = json.loads(report_path.read_text())
        except (OSError, ValueError) as error:
            raise Failure(f"{path}: scenario {name}: run report {report_path} unreadable ({error})")
        if report.get("test_outcome") != "pass":
            raise Failure(f"{report_path}: test_outcome is {report.get('test_outcome')!r}")
        if report.get("abi") != ACCEPTANCE_ABI[target]:
            raise Failure(f"{report_path}: ran on ABI {report.get('abi')!r}, not {ACCEPTANCE_ABI[target]}")
        if digest not in (report.get("input_files") or {}).values():
            raise Failure(f"{report_path}: {source.name} ({digest}) is not among the run's inputs")


def cmd_promote(args):
    """Copies an accepted candidate unchanged to dist/Helix-<version>-<target>.zip and
    adds it to dist/SHA256SUMS. --accepted names the acceptance record (see
    check_acceptance). The archive's files are checked against its contents list.
    An interrupted promotion leaves a journal (dist/.<name>.promoting) and is finished
    by running the same command again; without a journal nothing is replaced."""
    unreleased = REPO / "dist" / "unreleased"
    source = unreleased / f"Helix-{PACKAGE_VERSION}-candidate{args.candidate}-{args.target}.zip"
    listing = source.with_name(source.name[:-4] + ".contents.sha256")
    listed = (unreleased / f"SHA256SUMS-{PACKAGE_VERSION}").read_text().split("\n")
    digest = sha256_file(source)
    if f"{digest}  {source.name}" not in listed:
        raise Failure(f"{source.name} does not match its line in SHA256SUMS-{PACKAGE_VERSION}")
    check_acceptance(args.accepted, source, digest, args.target)
    with tempfile.TemporaryDirectory(prefix="hx-promote-") as tmp:
        run(["unzip", "-q", source, "-d", tmp])
        found = sorted(p for p in Path(tmp).rglob("*") if p.is_file())
        actual = "".join(f"{sha256_file(p)}  {p.relative_to(tmp).as_posix()}\n" for p in found)
        if actual != listing.read_text():
            raise Failure(f"{source.name}'s files do not match {listing.name}")
    dist = REPO / "dist"
    name = f"Helix-{PACKAGE_VERSION}-{args.target}"
    archive, contents = dist / f"{name}.zip", dist / f"{name}.contents.sha256"
    sums = dist / "SHA256SUMS"
    journal = dist / f".{name}.promoting"
    intent = {"source": source.name, "sha256": digest, "contents_sha256": sha256_file(listing)}
    sums_lines = sums.read_text().split("\n") if sums.exists() else []
    line = f"{digest}  {archive.name}"
    if journal.exists():
        # Resume only the promotion the journal names; anything else is someone else's.
        if json.loads(journal.read_text()) != intent:
            raise Failure(f"{journal} names another promotion; resolve it by hand")
    elif archive.exists() or contents.exists() or any(l.endswith(f"  {archive.name}") for l in sums_lines):
        raise Failure(f"{archive} exists; delivered archives are never replaced")
    else:
        part = journal.with_name(journal.name + ".part")
        part.write_text(json.dumps(intent))
        os.replace(part, journal)
    if any(l.endswith(f"  {archive.name}") and l != line for l in sums_lines):
        raise Failure(f"dist/SHA256SUMS has another digest for {archive.name}")
    # Each step checks what is already there, so a retry after any failure continues.
    for src, final, want in ((source, archive, digest), (listing, contents, intent["contents_sha256"])):
        if final.exists():
            if sha256_file(final) != want:
                raise Failure(f"{final} differs from {src.name}; resolve it by hand")
            continue
        part = final.with_name("." + final.name + ".part")
        try:
            shutil.copyfile(src, part)
            if sha256_file(part) != want:
                raise Failure(f"the copy of {src.name} differs from the candidate")
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
    if line not in sums_lines:
        with open(sums, "a") as out:
            out.write(line + "\n")
            out.flush()
            os.fsync(out.fileno())
    if line not in sums.read_text().split("\n"):
        raise Failure(f"{sums} lacks the line for {archive.name}")
    journal.unlink()
    print(archive)


def cmd_config(args):
    cfg = config(target=args.target)
    print(f"{'target':18} {cfg['target']}")
    for key, (env, _, description) in SETTINGS.items():
        print(f"{key:18} {cfg.get(key) or '(unset)':60} {env}: {description}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in (("config", cmd_config), ("materialize", cmd_materialize), ("export", cmd_export),
                     ("verify", cmd_verify), ("build", cmd_build), ("package", cmd_package),
                     ("promote", cmd_promote)):
        sp = sub.add_parser(name, help=fn.__doc__)
        sp.set_defaults(fn=fn)
        if name in ("config", "build", "package", "promote"):
            sp.add_argument("--target", choices=sorted(TARGETS), default=DEFAULT_TARGET)
        if name == "promote":
            sp.add_argument("--candidate", type=int, required=True, help="candidate number")
        if name == "package":
            sp.add_argument("--candidate", type=int, help="candidate number (dist/unreleased)")
            sp.add_argument("--iteration", type=int,
                            help="port iteration N: Helix-25.07.1-aros<N>-<abi>.zip straight into dist/")
            sp.add_argument("--draft", action="store_true",
                            help="with --iteration: write to <build root>/draft instead of dist/ (tests)")
        if name in ("build", "package"):
            sp.add_argument("--lsp-experimental", action="store_true",
                            help="the LSP build (language servers start; formatters, :sh, :pipe and debug adapters do not)")
        if name == "package":
            sp.add_argument("--server", help="language server build record (JSON) for --lsp-experimental")
            sp.add_argument("--unstripped", action="store_true",
                            help="pack the linked binary unstripped (diagnostic variant)")
        if name == "promote":
            sp.add_argument("--accepted", required=True,
                            help="acceptance record (JSON, schema helix-acceptance-1; see check_acceptance)")
        if name == "build":
            # Unwind (the default): a panic on the editor thread can then end the
            # program from the main task; AROS cannot abort() from a thread cleanly.
            sp.add_argument("--panic", choices=("unwind", "abort"), default=None)
            sp.add_argument("--probe", help="build experiments/<PROBE> instead of hx")
            sp.add_argument("--probe-flag", action="append", default=[],
                            help="extra rustc flag for a --probe build, e.g. -Ccode-model=small (diagnosis)")
            sp.add_argument("--no-abi-gate", action="store_true",
                            help="skip the layout gate (diagnostic rebuilds only; not packaged)")
    args = p.parse_args()
    try:
        args.fn(args)
    except Failure as error:
        sys.exit(f"hx.py: {error}")


if __name__ == "__main__":
    main()
