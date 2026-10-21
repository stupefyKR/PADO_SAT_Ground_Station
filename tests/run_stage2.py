"""Generate ephemeral TLS identities, then run the C transport tests."""

import pathlib
import subprocess
import sys
import tempfile


def run(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def identity(root: pathlib.Path, name: str, ip: str) -> None:
    run("openssl", "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
        "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.csr",
        "-subj", f"/CN={name}", cwd=root)
    (root / f"{name}.ext").write_text(
        f"subjectAltName=IP:{ip}\nextendedKeyUsage=serverAuth,clientAuth\n",
        encoding="ascii")
    run("openssl", "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt",
        "-CAkey", "ca.key", "-CAcreateserial", "-out", f"{name}.crt",
        "-days", "1", "-extfile", f"{name}.ext", cwd=root)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    executable = pathlib.Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="pado-stage2-") as temp:
        root = pathlib.Path(temp)
        run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
            "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key",
            "-out", "ca.crt", "-days", "1", "-subj", "/CN=PADO-Test-CA",
            cwd=root)
        identity(root, "server", "127.0.0.2")
        identity(root, "client", "127.0.0.3")
        return subprocess.run([str(executable), str(root / "server.crt"),
                               str(root / "server.key"), str(root / "client.crt"),
                               str(root / "client.key"), str(root / "ca.crt")],
                              check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
