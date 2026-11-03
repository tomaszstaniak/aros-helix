"""Host test of `hx.py promote`: refusals of acceptance records that are not a pass for
this archive and target, and resumption after a failure at each finalization step.
Runs in temporary directories; the real dist/ is not touched.

    python3 scripts/test_promote.py
"""
import contextlib,io
import importlib.util,tempfile,pathlib,zipfile,hashlib,types,json,sys
p=pathlib.Path(__file__).resolve().parent/'hx.py'
bad=[]
s=importlib.util.spec_from_file_location('hx',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
T='aros-x86_64-abiv11-pc'
def fixture(t):
    m.REPO=pathlib.Path(t);u=m.REPO/'dist/unreleased';u.mkdir(parents=True)
    z=u/f'Helix-{m.PACKAGE_VERSION}-candidate99-{T}.zip'
    with zipfile.ZipFile(z,'w') as a:a.writestr('Helix/hx',b'test fixture')
    h=hashlib.sha256(z.read_bytes()).hexdigest()
    (u/f'SHA256SUMS-{m.PACKAGE_VERSION}').write_text(f'{h}  {z.name}\n')
    z.with_suffix('.contents.sha256').write_text(hashlib.sha256(b'test fixture').hexdigest()+'  Helix/hx\n')
    return z,h
def record(t,z,h,**over):
    ev=pathlib.Path(t)/'ev';ev.mkdir(exist_ok=True)
    rep=dict(test_outcome='pass',abi='v11',input_files={'c.zip':h});rep.update(over.pop('report',{}))
    (ev/'REPORT.json').write_text(json.dumps(rep))
    r=dict(schema='helix-acceptance-1',artifact=z.name,sha256=h,target=T,outcome='pass',
           scenarios={n:{'outcome':'pass','report':'ev/REPORT.json'} for n in m.ACCEPTANCE_SCENARIOS})
    r.update(over);f=pathlib.Path(t)/'acc.json';f.write_text(json.dumps(r));return f
def promote(f):
    try:
        with contextlib.redirect_stdout(io.StringIO()):m.cmd_promote(types.SimpleNamespace(candidate=99,target=T,accepted=str(f)))
        return 'promoted'
    except m.Failure as e:return 'refused: '+str(e).split(': ',1)[-1]
cases={
 'free text with digest':lambda t,z,h:(pathlib.Path(t)/'a.txt').write_text(h) and pathlib.Path(t)/'a.txt',
 'old review fixture (fail)':lambda t,z,h:(pathlib.Path(t)/'f.json').write_text(json.dumps({'test_outcome':'fail','sha256':h})) and pathlib.Path(t)/'f.json',
 'outcome fail':lambda t,z,h:record(t,z,h,outcome='fail'),
 'outcome missing':lambda t,z,h:record(t,z,h,outcome=None),
 'wrong target':lambda t,z,h:record(t,z,h,target='aros-x86_64-abiv1-pc'),
 'wrong digest':lambda t,z,h:record(t,z,h,sha256='0'*64),
 'scenario missing':lambda t,z,h:record(t,z,h,scenarios={'edit-save':{'outcome':'pass','report':'ev/REPORT.json'}}),
 'scenario fail':lambda t,z,h:record(t,z,h,scenarios={n:{'outcome':'fail' if n=='split' else 'pass','report':'ev/REPORT.json'} for n in m.ACCEPTANCE_SCENARIOS}),
 'run failed':lambda t,z,h:record(t,z,h,report={'test_outcome':'fail'}),
 'run other ABI':lambda t,z,h:record(t,z,h,report={'abi':'v1'}),
 'run without archive':lambda t,z,h:record(t,z,h,report={'input_files':{'x':'1'*64}}),
 'valid':lambda t,z,h:record(t,z,h),
}
for name,mk in cases.items():
    with tempfile.TemporaryDirectory() as t:
        z,h=fixture(t);r=promote(mk(t,z,h));print(f'{name:28} {r}')
        if (r=='promoted')!=(name=='valid'):bad.append(name)
# interrupted finalization at each step, then retry
for step in ('rename1','rename2','sums'):
    with tempfile.TemporaryDirectory() as t:
        z,h=fixture(t);f=record(t,z,h)
        orep=m.os.replace;ofs=m.os.fsync;calls=[0]
        def rep(a,b):
            if 'promoting' not in str(b): calls[0]+=1
            if (step=='rename1' and calls[0]==1) or (step=='rename2' and calls[0]==2): raise OSError('injected')
            orep(a,b)
        def fs(fd):
            if step=='sums': raise OSError('injected')
            ofs(fd)
        m.os.replace=rep;m.os.fsync=fs
        try:
            with contextlib.redirect_stdout(io.StringIO()):m.cmd_promote(types.SimpleNamespace(candidate=99,target=T,accepted=str(f)))
        except OSError:pass
        m.os.replace=orep;m.os.fsync=ofs
        d=m.REPO/'dist';left=sorted(x.name for x in d.iterdir() if x.is_file())
        r=promote(f);d2=sorted(x.name for x in d.iterdir() if x.is_file())
        sums=(d/'SHA256SUMS').read_text().count('\n') if (d/'SHA256SUMS').exists() else 0
        print(f'fail at {step:8} left {left}\n   retry: {r}; now {d2}; sums lines {sums}')
        r2=promote(f);print('   second promote:',r2)
        if r!='promoted' or sums!=1 or r2=='promoted' or any(x.startswith('.') for x in d2):bad.append(step)
# foreign journal / existing archive
with tempfile.TemporaryDirectory() as t:
    z,h=fixture(t);f=record(t,z,h);d=m.REPO/'dist'
    (d/f'Helix-{m.PACKAGE_VERSION}-{T}.zip').write_bytes(b'other');r=promote(f);print('existing archive, no journal:',r)
    if r=='promoted':bad.append('existing')
with tempfile.TemporaryDirectory() as t:
    z,h=fixture(t);f=record(t,z,h);d=m.REPO/'dist'
    (d/f'.Helix-{m.PACKAGE_VERSION}-{T}.promoting').write_text('{"x":1}');r=promote(f);print('foreign journal:',r)
    if r=='promoted':bad.append('journal')
print('result:', 'ok' if not bad else f'FAILED {bad}')
sys.exit(1 if bad else 0)
