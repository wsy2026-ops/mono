"""MONO 社区 · 运行状态与攻击监控采集模块

设计目标：
    1. 记录服务器实时运行状态（CPU / 内存 / 磁盘 / 负载 / 网络 / 连接数）
    2. 记录所有被拦截的攻击事件（来源 IP + 归属地）
    3. **从最早记录点起持续留存，不做删除**
    4. 零第三方依赖（不装 psutil，直接读 /proc）

数据来源：
    - 服务器指标  → /proc/stat、/proc/meminfo、/proc/net/dev、/proc/uptime、os.statvfs
    - 应用拦截    → 应用在拦截点直接调用 note_attack()（内存计数，定期落盘）
    - Nginx 限流  → 增量解析 /var/log/nginx/mono-error.log
    - SSH 封禁    → 轮询 fail2ban-client status sshd
    - IP 归属地   → ip-api.com 批量接口（一次 100 个），结果永久缓存

三张表：
    monitor_samples  服务器指标采样（每 60 秒一条）
    attack_events    攻击/拦截事件（按 15 秒粒度聚合，带 count）
    ip_geo           IP → 国家/地区/城市/运营商（每个 IP 只查一次，永久缓存）

安全约束：本模块任何异常都不得影响主业务，所有入口 fail-open。
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

SAMPLE_INTERVAL = 60
FLUSH_INTERVAL = 15
GEO_INTERVAL = 300
GEO_BATCH_MAX = 100
NGINX_ERROR_LOG = "/var/log/nginx/mono-error.log"
# 采样保留期（天）。每 60 秒一条 ≈ 1450 行/天，永久保留的话一年就是 50 万行。
# 后台趋势最大只看 90 天，所以保留 100 天（多留 10 天缓冲，
# 免得「看 90 天」时最左边那段刚好被删掉）。
# ⚠️ 原来的 KEEP_FOREVER = True 是死标志 —— 定义了但全站没人引用，
#    实际行为就是「永不清理」。
RETAIN_DAYS = 100
PRUNE_INTERVAL = 6 * 3600      # 每 6 小时清一次，够用且不占 IO

TZ_OFFSET = 8 * 3600


def day_start_ts():
    """当地（站点时区）当天 0 点的时间戳。"""
    t = time.time() + TZ_OFFSET
    return (t - (t % 86400)) - TZ_OFFSET

_lock = threading.Lock()
_started = False
_db_path = None

COUNTRY_CN = {
    "United States": "美国", "China": "中国", "Hong Kong": "中国香港",
    "Taiwan": "中国台湾", "Macao": "中国澳门", "Macau": "中国澳门",
    "Russia": "俄罗斯", "Germany": "德国", "France": "法国",
    "Netherlands": "荷兰", "United Kingdom": "英国", "Brazil": "巴西",
    "India": "印度", "Japan": "日本", "South Korea": "韩国",
    "Korea, Republic of": "韩国", "Singapore": "新加坡", "Canada": "加拿大",
    "Australia": "澳大利亚", "Vietnam": "越南", "Thailand": "泰国",
    "Indonesia": "印度尼西亚", "Malaysia": "马来西亚", "Philippines": "菲律宾",
    "Turkey": "土耳其", "Türkiye": "土耳其", "Iran": "伊朗",
    "Iraq": "伊拉克", "Israel": "以色列", "Saudi Arabia": "沙特阿拉伯",
    "United Arab Emirates": "阿联酋", "Pakistan": "巴基斯坦",
    "Bangladesh": "孟加拉国", "Sri Lanka": "斯里兰卡", "Nepal": "尼泊尔",
    "Kazakhstan": "哈萨克斯坦", "Uzbekistan": "乌兹别克斯坦",
    "Ukraine": "乌克兰", "Belarus": "白俄罗斯", "Poland": "波兰",
    "Czechia": "捷克", "Czech Republic": "捷克", "Slovakia": "斯洛伐克",
    "Hungary": "匈牙利", "Romania": "罗马尼亚", "Bulgaria": "保加利亚",
    "Greece": "希腊", "Italy": "意大利", "Spain": "西班牙",
    "Portugal": "葡萄牙", "Switzerland": "瑞士", "Austria": "奥地利",
    "Belgium": "比利时", "Denmark": "丹麦", "Sweden": "瑞典",
    "Norway": "挪威", "Finland": "芬兰", "Iceland": "冰岛",
    "Ireland": "爱尔兰", "Luxembourg": "卢森堡", "Moldova": "摩尔多瓦",
    "Lithuania": "立陶宛", "Latvia": "拉脱维亚", "Estonia": "爱沙尼亚",
    "Slovenia": "斯洛文尼亚", "Croatia": "克罗地亚", "Serbia": "塞尔维亚",
    "Bosnia and Herzegovina": "波黑", "Albania": "阿尔巴尼亚",
    "North Macedonia": "北马其顿", "Montenegro": "黑山", "Georgia": "格鲁吉亚",
    "Armenia": "亚美尼亚", "Azerbaijan": "阿塞拜疆", "Cyprus": "塞浦路斯",
    "Malta": "马耳他", "Mexico": "墨西哥", "Argentina": "阿根廷",
    "Chile": "智利", "Colombia": "哥伦比亚", "Peru": "秘鲁",
    "Venezuela": "委内瑞拉", "Ecuador": "厄瓜多尔", "Bolivia": "玻利维亚",
    "Paraguay": "巴拉圭", "Uruguay": "乌拉圭", "Panama": "巴拿马",
    "Costa Rica": "哥斯达黎加", "Guatemala": "危地马拉", "Honduras": "洪都拉斯",
    "Nicaragua": "尼加拉瓜", "El Salvador": "萨尔瓦多", "Cuba": "古巴",
    "Dominican Republic": "多米尼加", "Jamaica": "牙买加", "Haiti": "海地",
    "South Africa": "南非", "Egypt": "埃及", "Nigeria": "尼日利亚",
    "Kenya": "肯尼亚", "Morocco": "摩洛哥", "Algeria": "阿尔及利亚",
    "Tunisia": "突尼斯", "Libya": "利比亚", "Ghana": "加纳",
    "Ethiopia": "埃塞俄比亚", "Tanzania": "坦桑尼亚", "Uganda": "乌干达",
    "Zimbabwe": "津巴布韦", "Zambia": "赞比亚", "Mozambique": "莫桑比克",
    "Angola": "安哥拉", "Cameroon": "喀麦隆", "Senegal": "塞内加尔",
    "Ivory Coast": "科特迪瓦", "Côte d'Ivoire": "科特迪瓦",
    "Sudan": "苏丹", "Somalia": "索马里", "Mali": "马里",
    "Burkina Faso": "布基纳法索", "Niger": "尼日尔", "Chad": "乍得",
    "Mauritius": "毛里求斯", "Madagascar": "马达加斯加", "Rwanda": "卢旺达",
    "New Zealand": "新西兰", "Mongolia": "蒙古", "Cambodia": "柬埔寨",
    "Laos": "老挝", "Myanmar": "缅甸", "Brunei": "文莱",
    "Afghanistan": "阿富汗", "Syria": "叙利亚", "Jordan": "约旦",
    "Lebanon": "黎巴嫩", "Kuwait": "科威特", "Qatar": "卡塔尔",
    "Bahrain": "巴林", "Oman": "阿曼", "Yemen": "也门",
    "Kyrgyzstan": "吉尔吉斯斯坦", "Tajikistan": "塔吉克斯坦",
    "Turkmenistan": "土库曼斯坦", "Bhutan": "不丹", "Maldives": "马尔代夫",
    "Papua New Guinea": "巴布亚新几内亚", "Fiji": "斐济",
    "Puerto Rico": "波多黎各", "Greenland": "格陵兰", "Bermuda": "百慕大",
    "Virgin Islands": "维尔京群岛", "Bahamas": "巴哈马", "Barbados": "巴巴多斯",
    "Trinidad and Tobago": "特立尼达和多巴哥", "Belize": "伯利兹",
    "Suriname": "苏里南", "Guyana": "圭亚那",
    "Bonaire, Sint Eustatius and Saba": "加勒比荷兰",
    "Curaçao": "库拉索", "Aruba": "阿鲁巴", "Gibraltar": "直布罗陀",
    "Isle of Man": "马恩岛", "Jersey": "泽西岛", "Guernsey": "根西岛",
    "Faroe Islands": "法罗群岛", "Andorra": "安道尔", "Monaco": "摩纳哥",
    "Liechtenstein": "列支敦士登", "San Marino": "圣马力诺",
    "Vatican City": "梵蒂冈", "Kosovo": "科索沃", "Palestine": "巴勒斯坦",
    "North Korea": "朝鲜", "Cape Verde": "佛得角", "Benin": "贝宁",
    "Togo": "多哥", "Guinea": "几内亚", "Sierra Leone": "塞拉利昂",
    "Liberia": "利比里亚", "Gabon": "加蓬", "Congo": "刚果",
    "Democratic Republic of the Congo": "刚果民主共和国",
    "Central African Republic": "中非", "Equatorial Guinea": "赤道几内亚",
    "Gambia": "冈比亚", "Guinea-Bissau": "几内亚比绍", "Mauritania": "毛里塔尼亚",
    "Djibouti": "吉布提", "Eritrea": "厄立特里亚", "Burundi": "布隆迪",
    "Malawi": "马拉维", "Botswana": "博茨瓦纳", "Namibia": "纳米比亚",
    "Lesotho": "莱索托", "Eswatini": "斯威士兰", "Seychelles": "塞舌尔",
    "Sao Tome and Principe": "圣多美和普林西比", "Comoros": "科摩罗",
    "Samoa": "萨摩亚", "Tonga": "汤加", "Vanuatu": "瓦努阿图",
    "Solomon Islands": "所罗门群岛", "New Caledonia": "新喀里多尼亚",
    "French Polynesia": "法属波利尼西亚", "Guam": "关岛",
    "Timor-Leste": "东帝汶", "European Union": "欧盟", "Europe": "欧洲",
}


def country_cn(name):
    """英文国家名 → 中文。查不到就原样返回（不丢信息）。"""
    if not name:
        return name
    n = name.strip()
    if n in COUNTRY_CN:
        return COUNTRY_CN[n]
    for prefix in ("The ", "the "):
        if n.startswith(prefix) and n[len(prefix):] in COUNTRY_CN:
            return COUNTRY_CN[n[len(prefix):]]
    return n


def _geo_cn(row):
    """给一行查询结果补上中文字段（原地修改并返回）。"""
    try:
        if row.get("country"):
            row["country_cn"] = country_cn(row["country"])
    except Exception:
        pass
    return row


_req_count = 0
_req_ms_total = 0
_block_count = 0

_pending = {}

_nginx_offset = 0
_known_banned = set()

_cpu_prev = None


def init(data_dir):
    """建表。可重复调用。返回 True 表示可用。"""
    global _db_path
    try:
        _db_path = os.path.join(data_dir, "data.db")
        db = sqlite3.connect(_db_path, timeout=10)
        db.execute("PRAGMA busy_timeout=5000")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS monitor_samples (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            cpu_pct       REAL,
            load1         REAL,
            load5         REAL,
            load15        REAL,
            mem_used_mb   INTEGER,
            mem_total_mb  INTEGER,
            swap_used_mb  INTEGER,
            swap_total_mb INTEGER,
            disk_used_gb  REAL,
            disk_total_gb REAL,
            net_rx_kb     INTEGER,
            net_tx_kb     INTEGER,
            conn_estab    INTEGER,
            proc_count    INTEGER,
            uptime_sec    INTEGER,
            app_threads   INTEGER,
            req_count     INTEGER,
            req_avg_ms    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_mon_ts ON monitor_samples(ts);

        CREATE TABLE IF NOT EXISTS attack_events (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     REAL NOT NULL,
            kind   TEXT NOT NULL,
            ip     TEXT NOT NULL,
            path   TEXT,
            count  INTEGER NOT NULL DEFAULT 1,
            detail TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_atk_ts ON attack_events(ts);
        CREATE INDEX IF NOT EXISTS idx_atk_ip ON attack_events(ip);
        CREATE INDEX IF NOT EXISTS idx_atk_kind ON attack_events(kind, ts);

        CREATE TABLE IF NOT EXISTS ip_geo (
            ip         TEXT PRIMARY KEY,
            country    TEXT,
            region     TEXT,
            city       TEXT,
            isp        TEXT,
            updated_at REAL
        );
        """)
        db.commit()
        db.close()
        return True
    except Exception as e:
        _log("初始化失败: %s" % e)
        return False


def start(data_dir):
    """启动后台采集线程。失败不影响主业务。"""
    global _started
    if _started:
        return
    if not init(data_dir):
        return
    _started = True
    t = threading.Thread(target=_loop, name="mono-monitor", daemon=True)
    t.start()
    _log("采集线程已启动（采样 %ds / 落盘 %ds / 归属地 %ds）"
         % (SAMPLE_INTERVAL, FLUSH_INTERVAL, GEO_INTERVAL))


def _log(msg):
    try:
        import mono_log
        mono_log.info("[监控] %s", msg)
    except Exception:
        pass


def _conn():
    _ensure()
    c = sqlite3.connect(_db_path, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    return c


def _ensure():
    """懒初始化：外部（如运维脚本）直接调用查询函数而没先 init 时，
    自动按 MONO_DATA_DIR / 模块所在目录推导数据库路径。

    没有这层兜底的话，_db_path 为空会让所有查询静默返回空结果，
    排查起来很费劲（这是实际踩到过的坑）。
    """
    global _db_path
    if _db_path:
        return True
    try:
        base = os.path.abspath(
            os.environ.get("MONO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__)))
        return init(base)
    except Exception:
        return False


def note_request(cost_ms):
    """应用每次响应后调用。只做内存自增，开销可忽略。"""
    global _req_count, _req_ms_total
    try:
        with _lock:
            _req_count += 1
            _req_ms_total += (cost_ms or 0)
    except Exception:
        pass


def note_attack(kind, ip, path=None, detail=None):
    """应用在拦截点调用。

    只写内存计数（按 kind+ip+path 聚合），由采集线程定期落盘 ——
    避免在请求路径上做数据库写入，也避免被攻击时把磁盘写爆。
    """
    global _block_count
    try:
        key = (kind, ip or "-", (path or "")[:120], (detail or "")[:80])
        with _lock:
            _pending[key] = _pending.get(key, 0) + 1
            _block_count += 1
    except Exception:
        pass


def take_request_stats():
    """取走并清零请求计数，返回 (请求数, 平均耗时ms)。"""
    global _req_count, _req_ms_total, _block_count
    try:
        with _lock:
            n, ms, b = _req_count, _req_ms_total, _block_count
            _req_count = 0
            _req_ms_total = 0
            _block_count = 0
        return n, (int(ms / n) if n else 0), b
    except Exception:
        return 0, 0, 0


def _cpu_percent():
    """CPU 使用率。需要两次采样求差值，首次返回 None。"""
    global _cpu_prev
    try:
        with open("/proc/stat", "r") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        if _cpu_prev is None:
            _cpu_prev = (idle, total)
            return None
        d_idle = idle - _cpu_prev[0]
        d_total = total - _cpu_prev[1]
        _cpu_prev = (idle, total)
        if d_total <= 0:
            return None
        return round((1.0 - d_idle / d_total) * 100.0, 1)
    except Exception:
        return None


def _meminfo():
    """返回 (已用MB, 总MB, Swap已用MB, Swap总MB)。"""
    try:
        vals = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                k, _, v = line.partition(":")
                vals[k.strip()] = int(v.split()[0]) if v.split() else 0
        total = vals.get("MemTotal", 0) // 1024
        avail = vals.get("MemAvailable", vals.get("MemFree", 0)) // 1024
        sw_total = vals.get("SwapTotal", 0) // 1024
        sw_free = vals.get("SwapFree", 0) // 1024
        return total - avail, total, sw_total - sw_free, sw_total
    except Exception:
        return None, None, None, None


def _disk():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize / (1024 ** 3)
        free = st.f_bavail * st.f_frsize / (1024 ** 3)
        return round(total - free, 2), round(total, 2)
    except Exception:
        return None, None


def _net_bytes():
    """累计收发字节（排除 lo）。"""
    try:
        rx = tx = 0
        with open("/proc/net/dev", "r") as f:
            for line in f.readlines()[2:]:
                name, _, data = line.partition(":")
                if name.strip() == "lo":
                    continue
                cols = data.split()
                rx += int(cols[0])
                tx += int(cols[8])
        return rx, tx
    except Exception:
        return None, None


def _conn_estab():
    """已建立（ESTABLISHED）的 TCP 连接数。"""
    n = 0
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, "r") as f:
                for line in f.readlines()[1:]:
                    cols = line.split()
                    if len(cols) > 3 and cols[3] == "01":
                        n += 1
        except Exception:
            pass
    return n


def _uptime():
    try:
        with open("/proc/uptime", "r") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return None


def _proc_count():
    try:
        return len([d for d in os.listdir("/proc") if d.isdigit()])
    except Exception:
        return None


def collect_sample(prev_net=None):
    """采集一条服务器指标。prev_net 用于算网络增量。"""
    cpu = _cpu_percent()
    mem_used, mem_total, sw_used, sw_total = _meminfo()
    disk_used, disk_total = _disk()
    rx, tx = _net_bytes()
    rx_kb = tx_kb = None
    if prev_net and rx is not None:
        rx_kb = max(0, (rx - prev_net[0]) // 1024)
        tx_kb = max(0, (tx - prev_net[1]) // 1024)
    load = None
    try:
        load = os.getloadavg()
    except Exception:
        pass
    req_n, req_avg, _blk = take_request_stats()
    return {
        "ts": time.time(),
        "cpu_pct": cpu,
        "load1": round(load[0], 2) if load else None,
        "load5": round(load[1], 2) if load else None,
        "load15": round(load[2], 2) if load else None,
        "mem_used_mb": mem_used, "mem_total_mb": mem_total,
        "swap_used_mb": sw_used, "swap_total_mb": sw_total,
        "disk_used_gb": disk_used, "disk_total_gb": disk_total,
        "net_rx_kb": rx_kb, "net_tx_kb": tx_kb,
        "conn_estab": _conn_estab(),
        "proc_count": _proc_count(),
        "uptime_sec": _uptime(),
        "app_threads": threading.active_count(),
        "req_count": req_n,
        "req_avg_ms": req_avg,
    }, (rx, tx)


_NGINX_RE = re.compile(
    r'limiting requests.*?client:\s*([0-9a-fA-F:.]+).*?request:\s*"(\w+)\s+([^"]*)"')


def _recently_recorded(kind, ip, seconds=86400):
    """判断该 (类型, IP) 在最近 N 秒内是否已经记录过。

    用途：fail2ban 的封禁列表是「当前状态」而非「事件流」，
    应用重启后会重新读到同一批封禁 IP。没有这层去重就会重复计数
    （实测：两次部署重启 → 同一批 IP 被记了两次）。
    """
    try:
        db = _conn()
        try:
            r = db.execute(
                "SELECT 1 FROM attack_events WHERE kind=? AND ip=? AND ts>=? LIMIT 1",
                (kind, ip, time.time() - seconds)).fetchone()
            return r is not None
        finally:
            db.close()
    except Exception:
        return False


def scan_nginx_log():
    """增量读取 nginx 错误日志，提取限流事件。返回新增条数。"""
    global _nginx_offset
    if not os.path.exists(NGINX_ERROR_LOG):
        return 0
    n = 0
    try:
        size = os.path.getsize(NGINX_ERROR_LOG)
        if size < _nginx_offset:
            _nginx_offset = 0
        if size == _nginx_offset:
            return 0
        with open(NGINX_ERROR_LOG, "r", errors="replace") as f:
            f.seek(_nginx_offset)
            for line in f:
                m = _NGINX_RE.search(line)
                if m:
                    ip, _method, path = m.group(1), m.group(2), m.group(3)
                    note_attack("nginx_rate", ip, path, "Nginx 连接层限流")
                    n += 1
            _nginx_offset = f.tell()
    except Exception as e:
        _log("解析 nginx 日志失败: %s" % e)
    return n


def scan_fail2ban():
    """查询 fail2ban 已封禁 IP，把新增的记为事件。返回新增条数。"""
    n = 0
    try:
        import subprocess
        out = subprocess.run(
            ["fail2ban-client", "status", "sshd"],
            capture_output=True, text=True, timeout=8).stdout
        m = re.search(r"Banned IP list:\s*(.*)", out)
        if not m:
            return 0
        now = set(m.group(1).split())
        new = now - _known_banned
        _known_banned.clear()
        _known_banned.update(now)
        for ip in new:
            if _recently_recorded("ssh_ban", ip, 86400):
                continue
            note_attack("ssh_ban", ip, None, "fail2ban 自动封禁（SSH 爆破）")
            n += 1
    except Exception:
        pass
    return n


def flush_pending():
    """把内存里累积的攻击事件写入数据库。返回写入条数。"""
    global _pending
    try:
        with _lock:
            if not _pending:
                return 0
            batch = _pending
            _pending = {}
        now = time.time()
        db = _conn()
        try:
            db.executemany(
                "INSERT INTO attack_events (ts, kind, ip, path, count, detail) "
                "VALUES (?,?,?,?,?,?)",
                [(now, k[0], k[1], k[2], c, (k[3] or None) if len(k) > 3 else None)
                 for k, c in batch.items()])
            db.commit()
        finally:
            db.close()
        return len(batch)
    except Exception as e:
        _log("落盘失败: %s" % e)
        return 0


def _fetch_geo(ips):
    """批量查归属地。返回 {ip: (country, region, city, isp)}。"""
    result = {}
    if not ips:
        return result
    try:
        body = json.dumps(ips[:GEO_BATCH_MAX]).encode()
        req = urllib.request.Request(
            "http://ip-api.com/batch?fields=status,country,regionName,city,isp,query",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            arr = json.loads(r.read().decode("utf-8", "replace"))
        for it in arr:
            if it.get("status") == "success":
                result[it.get("query")] = (
                    it.get("country") or "", it.get("regionName") or "",
                    it.get("city") or "", it.get("isp") or "")
    except Exception as e:
        _log("归属地批量查询失败: %s" % e)
    for ip in ips:
        if ip in result:
            continue
        try:
            with urllib.request.urlopen(
                    "https://ipinfo.io/%s/json" % ip, timeout=8) as r:
                it = json.loads(r.read().decode("utf-8", "replace"))
            result[ip] = (it.get("country") or "", it.get("region") or "",
                          it.get("city") or "", (it.get("org") or ""))
        except Exception:
            break
    return result


def refresh_geo():
    """给还没归属地的 IP 补查。返回新增条数。"""
    try:
        db = _conn()
        try:
            rows = db.execute(
                "SELECT DISTINCT a.ip FROM attack_events a "
                "LEFT JOIN ip_geo g ON g.ip = a.ip "
                "WHERE g.ip IS NULL LIMIT ?", (GEO_BATCH_MAX,)).fetchall()
            todo = [r[0] for r in rows if r[0] and r[0] != "-"]
            if not todo:
                return 0
            geo = _fetch_geo(todo)
            now = time.time()
            for ip, v in geo.items():
                db.execute(
                    "INSERT INTO ip_geo (ip, country, region, city, isp, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(ip) DO UPDATE SET "
                    "country=excluded.country, region=excluded.region, "
                    "city=excluded.city, isp=excluded.isp, updated_at=excluded.updated_at",
                    (ip, v[0], v[1], v[2], v[3], now))
            for ip in todo:
                if ip not in geo:
                    db.execute(
                        "INSERT OR IGNORE INTO ip_geo (ip, country, region, city, isp, updated_at) "
                        "VALUES (?,?,?,?,?,?)", (ip, "未知", "", "", "", now))
            db.commit()
            return len(todo)
        finally:
            db.close()
    except Exception as e:
        _log("归属地刷新失败: %s" % e)
        return 0


def prune(retain_days=None):
    """删除超过保留期的采样。返回删除行数。

    只清 monitor_samples —— attack_events 才 3 千多行 / 0.3MB，
    而且后台「攻击来源」支持「全部时间」，删了会丢历史统计，不划算。
    """
    days = RETAIN_DAYS if retain_days is None else retain_days
    if not days or days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    db = _conn()
    try:
        cur = db.execute("DELETE FROM monitor_samples WHERE ts < ?", (cutoff,))
        n = cur.rowcount or 0
        db.commit()
        return n
    except Exception as e:
        _log("清理过期采样失败（已忽略）: %s" % e)
        return 0
    finally:
        db.close()


def _loop():
    global _nginx_offset
    prev_net = None
    last_sample = 0.0
    last_geo = 0.0
    last_prune = 0.0
    try:
        if os.path.exists(NGINX_ERROR_LOG):
            _nginx_offset = os.path.getsize(NGINX_ERROR_LOG)
    except Exception:
        pass

    while True:
        try:
            now = time.time()
            scan_nginx_log()
            scan_fail2ban()
            flush_pending()

            if now - last_sample >= SAMPLE_INTERVAL:
                last_sample = now
                sample, prev_net = collect_sample(prev_net)
                db = _conn()
                try:
                    cols = list(sample.keys())
                    db.execute(
                        "INSERT INTO monitor_samples (%s) VALUES (%s)"
                        % (",".join(cols), ",".join("?" * len(cols))),
                        [sample[c] for c in cols])
                    db.commit()
                finally:
                    db.close()

            if now - last_geo >= GEO_INTERVAL:
                last_geo = now
                n = refresh_geo()
                if n:
                    _log("已补齐 %d 个 IP 的归属地" % n)

            if now - last_prune >= PRUNE_INTERVAL:
                last_prune = now
                n = prune()
                if n:
                    _log("清理 %d 条过期采样（保留 %d 天）" % (n, RETAIN_DAYS))
        except Exception as e:
            _log("采集循环异常（已忽略）: %s" % e)
        time.sleep(FLUSH_INTERVAL)


def latest_sample():
    try:
        db = _conn()
        try:
            db.row_factory = sqlite3.Row
            r = db.execute(
                "SELECT * FROM monitor_samples ORDER BY ts DESC LIMIT 1").fetchone()
            return dict(r) if r else None
        finally:
            db.close()
    except Exception:
        return None


def sample_series(minutes=180, max_points=180):
    """取最近 N 分钟的采样，按桶 AVG 聚合到 max_points 个点。

    ⚠️ 2026-11-01 改了两处：
    ① 原来是「全拉出来再在 Python 里等间隔抽样」。90 天 = 13 万行 × 8 列，
       光 dict 就 60MB+，2G 的小机器扛不住。改成 SQL 里 AVG 聚合。
       短区间（≤3h）桶宽就等于采样间隔（60s），结果跟原始点完全一样。
    ② 原来 SELECT 漏了 mem_total_mb —— 前端算内存占用率要 (used/total)，
       拿不到 total 就永远是 null，**「内存 %」那条线一直是空的**。这里补上。
    """
    try:
        minutes = max(5, int(minutes))
    except (TypeError, ValueError):
        minutes = 180
    try:
        max_points = max(10, int(max_points))
    except (TypeError, ValueError):
        max_points = 180
    since = time.time() - minutes * 60
    bucket = max(SAMPLE_INTERVAL, int(minutes * 60 / max_points))
    try:
        db = _conn()
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT CAST((ts - ?) / ? AS INTEGER) b, "
                "       AVG(cpu_pct) cpu_pct, AVG(mem_used_mb) mem_used_mb, "
                "       AVG(mem_total_mb) mem_total_mb, AVG(load1) load1, "
                "       AVG(net_rx_kb) net_rx_kb, AVG(net_tx_kb) net_tx_kb, "
                "       AVG(conn_estab) conn_estab, AVG(req_count) req_count "
                "FROM monitor_samples WHERE ts >= ? GROUP BY b ORDER BY b",
                (since, bucket, since)).fetchall()
        finally:
            db.close()
        out = []
        for r in rows:
            d = dict(r)
            b = d.pop("b", 0) or 0
            d["ts"] = since + b * bucket
            out.append(d)
        return out
    except Exception:
        return []


def attack_summary():
    """攻击总览：总量、今日量、按类型分布、最早记录时间。"""
    out = {"total": 0, "today": 0, "first_ts": None, "by_kind": [], "blocked_total": 0}
    try:
        db = _conn()
        try:
            db.row_factory = sqlite3.Row
            r = db.execute("SELECT COALESCE(SUM(count),0) c, MIN(ts) f "
                           "FROM attack_events").fetchone()
            out["total"] = r["c"] or 0
            out["first_ts"] = r["f"]
            day_start = day_start_ts()
            r2 = db.execute("SELECT COALESCE(SUM(count),0) c FROM attack_events "
                            "WHERE ts >= ?", (day_start,)).fetchone()
            out["today"] = r2["c"] or 0
            for row in db.execute(
                    "SELECT kind, SUM(count) c FROM attack_events "
                    "GROUP BY kind ORDER BY c DESC").fetchall():
                out["by_kind"].append({"kind": row["kind"], "count": row["c"]})
            r3 = db.execute("SELECT COALESCE(SUM(req_count),0) c "
                            "FROM monitor_samples").fetchone()
            out["blocked_total"] = r3["c"] or 0
        finally:
            db.close()
    except Exception:
        pass
    return out


def top_attack_ips(limit=30, days=None):
    """攻击来源 IP 排行（带归属地）。"""
    try:
        where, args = "", []
        if days:
            where = "WHERE a.ts >= ?"
            args.append(time.time() - days * 86400)
        db = _conn()
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT a.ip, SUM(a.count) c, MAX(a.ts) last_ts, "
                "GROUP_CONCAT(DISTINCT a.kind) kinds, "
                "g.country, g.region, g.city, g.isp "
                "FROM attack_events a LEFT JOIN ip_geo g ON g.ip = a.ip "
                "%s GROUP BY a.ip ORDER BY c DESC LIMIT ?" % where,
                args + [limit]).fetchall()
            return [_geo_cn(dict(r)) for r in rows]
        finally:
            db.close()
    except Exception:
        return []


def attack_series(minutes=180, max_points=90):
    """最近 N 分钟的攻击事件时间序列（按桶聚合）。"""
    try:
        now = time.time()
        since = now - minutes * 60
        bucket = max(60, int(minutes * 60 / max_points))
        db = _conn()
        try:
            rows = db.execute(
                "SELECT CAST((ts - ?) / ? AS INTEGER) b, SUM(count) c "
                "FROM attack_events WHERE ts >= ? GROUP BY b ORDER BY b",
                (since, bucket, since)).fetchall()
        finally:
            db.close()
        out = []
        for i in range(max_points + 1):
            t = since + i * bucket
            out.append({"ts": t, "count": 0})
        for b, c in rows:
            if 0 <= b < len(out):
                out[b]["count"] = c
        return out
    except Exception:
        return []


def recent_events(limit=50, offset=0):
    """事件明细（按时间倒序，支持分页）。"""
    try:
        db = _conn()
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT a.ts, a.kind, a.ip, a.path, a.count, a.detail, "
                "g.country, g.region, g.city "
                "FROM attack_events a LEFT JOIN ip_geo g ON g.ip = a.ip "
                "ORDER BY a.ts DESC LIMIT ? OFFSET ?", (limit, max(0, offset))).fetchall()
            return [_geo_cn(dict(r)) for r in rows]
        finally:
            db.close()
    except Exception:
        return []


def count_events():
    """事件总条数（用于前端判断是否还有更多）。"""
    try:
        db = _conn()
        try:
            return db.execute("SELECT COUNT(*) FROM attack_events").fetchone()[0]
        finally:
            db.close()
    except Exception:
        return 0


def geo_distribution(days=None, limit=20):
    """攻击来源国家/地区分布。"""
    try:
        where, args = "", []
        if days:
            where = "WHERE a.ts >= ?"
            args.append(time.time() - days * 86400)
        db = _conn()
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT COALESCE(NULLIF(g.country,''),'未知') country, "
                "COUNT(DISTINCT a.ip) ips, SUM(a.count) c "
                "FROM attack_events a LEFT JOIN ip_geo g ON g.ip = a.ip "
                "%s GROUP BY country ORDER BY c DESC LIMIT ?" % where,
                args + [limit]).fetchall()
            return [_geo_cn(dict(r)) for r in rows]
        finally:
            db.close()
    except Exception:
        return []


def service_status():
    """各服务运行状态（systemd）。"""
    out = {}
    try:
        import subprocess
        for svc in ("mono", "nginx", "fail2ban", "ufw"):
            try:
                r = subprocess.run(["systemctl", "is-active", svc],
                                   capture_output=True, text=True, timeout=5)
                out[svc] = r.stdout.strip() or "unknown"
            except Exception:
                out[svc] = "unknown"
    except Exception:
        pass
    return out
