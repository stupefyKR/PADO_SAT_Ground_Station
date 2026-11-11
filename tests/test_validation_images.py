"""Import the runtime COPY payloads without borrowing modules from checkout."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ValidationImageImports(unittest.TestCase):
    def test_web_and_link_images_include_runtime_dependencies(self):
        (ROOT/'out').mkdir(exist_ok=True)
        for name,modules in [('Dockerfile',['services.webapp.main','services.validation.recorder','services.sim_ground.tc_journal']),
                             ('SimTm.Dockerfile',['services.sim_sat.emulator','services.sim_ground.receiver'])]:
            with self.subTest(image=name),tempfile.TemporaryDirectory(dir=ROOT/'out') as folder:
                app=Path(folder)
                for line in (ROOT/'docker'/name).read_text().splitlines():
                    if not line.startswith('COPY ') or line.startswith('COPY --from='):continue
                    parts=shlex.split(line)[1:];target=parts[-1]
                    if not target.startswith('/app/'):continue
                    for source in parts[:-1]:
                        original=ROOT/source
                        destination=app/target.removeprefix('/app/')
                        if target.endswith('/'):destination=destination/original.name
                        destination.parent.mkdir(parents=True,exist_ok=True)
                        if original.is_dir():shutil.copytree(original,destination,dirs_exist_ok=True)
                        else:shutil.copyfile(original,destination)
                result=subprocess.run([sys.executable,'-c','; '.join('import '+m for m in modules)],
                    cwd=app,env=dict(os.environ,PYTHONPATH=str(app)),capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
