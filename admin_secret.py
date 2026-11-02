# -*- coding: utf-8 -*-
"""管理员通行证校验。

通行证不写进源码：盐和 PBKDF2 哈希都从环境变量读。
未配置时校验一律失败 —— 开源项目绝不能带一个「人人相同」的默认口令，
否则谁都能在自己的实例上试出同一个通行证。

生成一对：
    python admin_secret.py --gen

把输出的两行写进环境变量（.env、systemd 的 Environment= 或 Docker -e）：

    MONO_ADMIN_SALT=<盐>
    MONO_ADMIN_HASH=<哈希>
"""
import binascii
import hashlib
import hmac
import os

_ALGORITHM = "sha256"
_ITERATIONS = 200000


def _params():
    salt = (os.environ.get("MONO_ADMIN_SALT") or "").strip()
    digest = (os.environ.get("MONO_ADMIN_HASH") or "").strip()
    return salt, digest


def available():
    """通行证是否已配置。没配置时 /become-admin 应当直接拒绝。"""
    salt, digest = _params()
    return bool(salt and digest)


def verify_admin_passcode(passcode):
    """校验输入的管理员通行证是否正确（恒定时间比较，防时序攻击）。"""
    salt_hex, hash_hex = _params()
    if not (salt_hex and hash_hex) or not passcode:
        return False
    try:
        salt = binascii.unhexlify(salt_hex)
    except (ValueError, binascii.Error):
        return False
    calc = hashlib.pbkdf2_hmac(_ALGORITHM, passcode.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(binascii.hexlify(calc).decode(), hash_hex.lower())


def make(passcode, salt=None):
    """返回 (salt_hex, hash_hex)。salt 不传就随机生成。"""
    salt = salt or os.urandom(16)
    calc = hashlib.pbkdf2_hmac(_ALGORITHM, passcode.encode("utf-8"), salt, _ITERATIONS)
    return binascii.hexlify(salt).decode(), binascii.hexlify(calc).decode()


def _cli():
    import getpass
    print("设置新的管理员通行证（输入不回显）")
    p1 = getpass.getpass("通行证：")
    if len(p1) < 8:
        raise SystemExit("太短了，至少 8 位")
    if p1 != getpass.getpass("再输一次："):
        raise SystemExit("两次输入不一致")
    salt_hex, hash_hex = make(p1)
    print("\n把下面两行加到环境变量里：\n")
    print("MONO_ADMIN_SALT=%s" % salt_hex)
    print("MONO_ADMIN_HASH=%s" % hash_hex)
    print("\n（设置完重启服务生效。这两个值不要提交进仓库。）")


if __name__ == "__main__":
    import sys
    if "--gen" in sys.argv:
        _cli()
    else:
        print(__doc__)
