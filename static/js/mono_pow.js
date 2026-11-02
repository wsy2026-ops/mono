/* mono_pow.js — 纯 JS SHA-256 兜底求解器（crypto.subtle 缺失的旧 WebView 用）
 *
 * 背景：register.html 的 PoW（工作量证明）用 crypto.subtle.digest，
 * 旧版微信 X5 内核 / 非安全上下文里 crypto.subtle = undefined，
 * 原来走「跳过」分支 → 服务端 PoW 校验必失败 → 注册死锁
 * （前端说「当前环境跳过算力验证」，提交却说「请先完成人机验证」）。
 *
 * 现在：前端探测到无 subtle 时领 nocrypto 低难度题（8 bits ≈ 256 次哈希），
 * 用这里同步算，0.x 秒完成。服务端校验逻辑不变，只是难度按环境分发。
 *
 * 用法：monoPow(chal, bits) -> sol 字符串（找不到返回 null）。
 */
(function (window) {
  'use strict';

  /* ---- 最小 SHA-256（同步、无依赖）---- */
  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function sha256(bytes) {
    var h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var l = bytes.length;
    var withOne = l + 1;
    var blocks = Math.ceil((withOne + 8) / 64);
    var total = blocks * 64;
    var m = new Array(total);
    var i;
    for (i = 0; i < l; i++) m[i] = bytes[i];
    m[l] = 0x80;
    for (i = withOne; i < total - 8; i++) m[i] = 0;
    var bitLen = l * 8;
    for (i = 0; i < 8; i++) m[total - 1 - i] = (bitLen >>> (8 * i)) & 0xff;

    var w = new Array(64);
    for (var b = 0; b < blocks; b++) {
      for (i = 0; i < 16; i++) {
        w[i] = (m[b * 64 + i * 4] << 24) | (m[b * 64 + i * 4 + 1] << 16) |
               (m[b * 64 + i * 4 + 2] << 8) | m[b * 64 + i * 4 + 3];
      }
      for (i = 16; i < 64; i++) {
        var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (i = 0; i < 64; i++) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & bb) ^ (a & c) ^ (bb & c);
        var t2 = (S0 + maj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0;
        d = c; c = bb; bb = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + bb) | 0; h[2] = (h[2] + c) | 0;
      h[3] = (h[3] + d) | 0; h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0;
      h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
    }
    var out = new Array(32);
    for (i = 0; i < 8; i++) {
      out[i * 4] = (h[i] >>> 24) & 0xff;
      out[i * 4 + 1] = (h[i] >>> 16) & 0xff;
      out[i * 4 + 2] = (h[i] >>> 8) & 0xff;
      out[i * 4 + 3] = h[i] & 0xff;
    }
    return out;
  }

  /* UTF-8 编码（挑战串是 ASCII，够用即可） */
  function utf8(s) {
    var out = [];
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) { out.push(0xc0 | (c >> 6), 0x80 | (c & 63)); }
      else { out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63)); }
    }
    return out;
  }

  /* 与 crypto.subtle 版 solvePow 同语义：sha256(chal|sol) 前 bits 个 bit 为 0 */
  function monoPow(chal, bits, limit) {
    limit = limit || 4000000;
    var prefix = chal + '|';
    for (var n = 0; n <= limit; n++) {
      var d = sha256(utf8(prefix + n));
      var need = bits, ok = true;
      for (var i = 0; i < 32 && need > 0; i++) {
        var byte = d[i];
        if (need >= 8) {
          if (byte !== 0) { ok = false; break; }
          need -= 8;
        } else {
          if (byte & ((1 << need) - 1)) { ok = false; break; }
          need = 0;
        }
      }
      if (ok) return String(n);
    }
    return null;
  }

  window.monoPow = monoPow;
})(window);
