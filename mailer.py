import os
import secrets
import smtplib
import ssl
import time
from email.header import Header
from email.mime.text import MIMEText

import requests

RESEND_API_KEY = os.environ.get('MONO_RESEND_KEY') or ''
RESEND_FROM = os.environ.get('MONO_RESEND_FROM') or ''
RESEND_ENDPOINT = 'https://api.resend.com/emails'

SMTP_HOST = os.environ.get('MONO_SMTP_HOST') or ''
SMTP_PORT = int(os.environ.get('MONO_SMTP_PORT') or 465)
SMTP_USER = os.environ.get('MONO_SMTP_USER') or ''
SMTP_PASS = os.environ.get('MONO_SMTP_PASS') or ''
SMTP_FROM = os.environ.get('MONO_SMTP_FROM') or SMTP_USER

CODE_TTL = 600
RESEND_RESEND_GAP = 60
SEND_PER_HOUR = 5


def smtp_available():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def available():
    return bool((RESEND_API_KEY and RESEND_FROM) or smtp_available())


def _send_smtp(to_addr, subject, body):
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = SMTP_FROM
    msg['To'] = to_addr
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=ctx) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_FROM, [to_addr], msg.as_string())
    return True


def _from_header():
    """Resend 发件人。

    环境变量里只存**纯地址**（noreply@monocraft.top）—— systemd 的 Environment=
    会把空格后的内容当成下一个 assignment 吃掉（2026-10-24 实测踩坑），
    所以显示名在这里拼。环境变量自带 '<' 的完整格式则原样使用。
    """
    v = (RESEND_FROM or '').strip()
    if not v:
        return ''
    if '<' in v:
        return v
    return 'MONO社区 <%s>' % v


def _send_resend(to_addr, subject, body):
    r = requests.post(
        RESEND_ENDPOINT,
        headers={'Authorization': 'Bearer ' + RESEND_API_KEY},
        json={'from': _from_header(), 'to': [to_addr], 'subject': subject, 'text': body},
        timeout=15)
    return r.status_code in (200, 201)


def deliver(to_addr, subject, body):
    """发一封邮件。**Resend 优先，失败自动回退 SMTP。**

    ⚠️ 2026-11-01 改：原来是 SMTP 优先，只要环境变量里配了 SMTP
    （哪怕 smtp.163.com 根本连不上）就永远走它、永远失败 ——
    注册验证码 / 找回密码 / 邮箱绑定全部哑火，而且没人报错。
    现在改成「谁能用就用谁」，两条路都试过才算失败。
    """
    errors = []
    if RESEND_API_KEY and RESEND_FROM:
        try:
            if _send_resend(to_addr, subject, body):
                return True
            errors.append('resend: 返回非 200')
        except Exception as e:
            errors.append('resend: %s' % e)
    if smtp_available():
        try:
            return _send_smtp(to_addr, subject, body)
        except Exception as e:
            errors.append('smtp: %s' % e)
    if errors:
        raise RuntimeError('; '.join(errors))
    raise RuntimeError('no mail backend')


def _row_get(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _tables(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS email_codes (
            email      TEXT NOT NULL,
            purpose    TEXT NOT NULL,
            code_hash  TEXT NOT NULL,
            sent_at    REAL NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL DEFAULT 0,
            used       INTEGER NOT NULL DEFAULT 0,
            tries      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (email, purpose)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS email_send_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL,
            purpose    TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_emsend_created ON email_send_log(created_at)")
    db.commit()


def _today_start():
    t = time.mktime(time.localtime())
    return t - (t % 86400)


def _hash(email, purpose, code):
    import hashlib
    return hashlib.sha256(('%s|%s|%s' % (email, purpose, code)).encode('utf-8')).hexdigest()


def send_code(db, email, purpose):
    """给邮箱发一条验证码。返回 (ok, 提示语)。"""
    email = (email or '').strip().lower()
    if not available():
        return False, '邮件服务未配置'
    if not valid_email(email):
        return False, '邮箱格式不对'

    day = _today_start()
    sent_hour = db.execute(
        "SELECT COUNT(*) c FROM email_send_log WHERE created_at>=?", (day,)).fetchone()['c']
    if sent_hour >= SEND_PER_HOUR * 24:
        return False, '今天发信太多了，稍后再试'
    per = db.execute(
        "SELECT COUNT(*) c FROM email_send_log WHERE email=? AND created_at>=?",
        (email, day)).fetchone()['c']
    if per >= 10:
        return False, '这个邮箱今天发得太多了，明天再来'

    _tables(db)
    row = db.execute("SELECT * FROM email_codes WHERE email=? AND purpose=?",
                     (email, purpose)).fetchone()
    if row and time.time() - (_row_get(row, 'sent_at') or 0) < RESEND_RESEND_GAP:
        return False, '刚发过，等一会儿再试'

    code = '%06d' % secrets.randbelow(1000000)
    body = ('你的验证码是 %s，%d 分钟内有效。\n\n'
            '如果不是本人操作，忽略这条消息即可，不要把验证码告诉别人。'
            % (code, CODE_TTL // 60))
    try:
        deliver(email, 'MONO 社区验证码', body)
        ok = True
    except Exception:
        ok = False

    if not ok:
        return False, '邮件发送失败，稍后再试'

    # ⚠️ 2026-11-01 修：原来 SQL 只有 5 个 ?，却传了 6 个参数（多一个 0），
    # 每次发验证码都 ProgrammingError 500 —— 邮箱验证码功能从来没成功过。
    db.execute("INSERT OR REPLACE INTO email_codes "
               "(email, purpose, code_hash, sent_at, expires_at, used, tries) "
               "VALUES (?,?,?,?,?,0,0)",
               (email, purpose, _hash(email, purpose, code), time.time(),
                time.time() + CODE_TTL))
    db.execute("INSERT INTO email_send_log (email, purpose, created_at) VALUES (?,?,?)",
               (email, purpose, time.time()))
    db.commit()
    return True, '验证码已发送'


def verify_code(db, email, purpose, code):
    """校验验证码。返回 (ok, 提示语)。校验通过即作废。"""
    email = (email or '').strip().lower()
    code = (code or '').strip()
    _tables(db)
    row = db.execute("SELECT * FROM email_codes WHERE email=? AND purpose=?",
                     (email, purpose)).fetchone()
    if not row:
        return False, '请先获取验证码'
    if _row_get(row, 'used'):
        return False, '验证码已使用过'
    if time.time() > (_row_get(row, 'expires_at') or 0):
        return False, '验证码已过期，重新获取'
    if (_row_get(row, 'tries') or 0) >= 5:
        return False, '尝试次数过多，重新获取验证码'
    if _hash(email, purpose, code) != _row_get(row, 'code_hash'):
        db.execute("UPDATE email_codes SET tries=tries+1 WHERE email=? AND purpose=?",
                   (email, purpose))
        db.commit()
        return False, '验证码不对'
    db.execute("UPDATE email_codes SET used=1 WHERE email=? AND purpose=?",
               (email, purpose))
    db.commit()
    return True, ''


def valid_email(email):
    email = (email or '').strip()
    if not email or len(email) > 190:
        return False
    if email.count('@') != 1:
        return False
    local, _, domain = email.partition('@')
    if not local or not domain or ' ' in email:
        return False
    if '..' in domain or '..' in local:
        return False
    labels = domain.split('.')
    if len(labels) < 2:
        return False
    for lb in labels:
        if not lb or len(lb) > 63 or lb.startswith('-') or lb.endswith('-'):
            return False
        for c in lb.lower():
            if not (('a' <= c <= 'z') or ('0' <= c <= '9') or c == '-'):
                return False
    tail = labels[-1]
    return len(tail) >= 2 and tail.isalpha()


def set_email(db, user_id, email):
    db.execute("UPDATE users SET email=? WHERE id=?", (email, user_id))
    db.commit()


def email_of(row):
    return _row_get(row, 'email') or ''