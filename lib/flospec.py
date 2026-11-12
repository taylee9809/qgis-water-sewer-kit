# -*- coding: utf-8 -*-
"""makesw .flo 읽기/쓰기/검증 — 표준 라이브러리만 사용 (동료 PC 이식용 단일 파일)

규격 근거: 실무 정상 .flo 198개 전수조사 (여러 설계사·지자체의 정상 작동 실적본). makesw 는 고정폭이 아니라 공백토큰 기반으로 파싱한다 — 같은 정상 파일
안에서도 탭 위치가 98~107 로 흔들린다. 따라서 '컬럼 폭 보존'은 불필요하고 토큰 규칙만 지키면 된다.
(2026-09-28 확인. 09-14판 실패 원인은 폭 어긋남이 아니라 방류 접두 예약어 위반이었다.)

레코드 = <머리> TAB <꼬리>
  머리: 상류맨홀 하류맨홀 연장 배수면적 유출계수 상류관저고 하류관저고 [조도n] 지반고[ 지반고2 지반고3] 플래그
        - 조도 n 은 첫 레코드에만 쓰고 이후 상속(sticky). 0.0xx 형태.
        - 지반고는 1개, 또는 압송 말단줄에서 3개(하류·방류 지반고 추가).
  꼬리: 관경 [우수파라미터 5개(첫 레코드만)] [Q=<m3/s>] [n=<조도>] [주석 x= G= PUMP BACKDROP …]

사용법
  python flospec.py <파일.flo> [...]           # 검증
  python flospec.py --fix-outfall <파일.flo>   # 방류 명명위반 자동수정본(_fixed) 생성
"""
import sys, os, re, collections

ENC, NL = 'cp949', '\n'
RE_N = re.compile(r'0\.0\d\d?$')                 # 조도계수 토큰
RE_NUM = re.compile(r'-?\d+(\.\d+)?$')           # 순수 수치 토큰
RE_DIA = re.compile(r'\d+(\.\d+)?$')             # 관경: 정수(mm) 또는 소수(m, BOX·대구경)
RE_BANGRYU_OK = re.compile(r'방류\d*(-\d+)?$')    # 방류 접두 허용형 (198파일 전수 근거)
MAX_NAME_BYTES = 18                              # '가나(다라)처리시설' 18바이트 정상작동 실적 상한


class Rec(dict):
    pass


def parse_head(toks):
    """머리 토큰 → 필드.
    - 3번째 토큰이 수치가 아니면 '지선 합류관 상류맨홀'(via) 선언이다 (일부 실적본 변종).
    - 조도 n 은 선택. 지반고는 1~3개."""
    up, dn = toks[0], toks[1]
    i, via = 2, None
    if not RE_NUM.match(toks[2]):
        via = toks[2]
        i = 3
    L, area, coef, ilu, ild = toks[i:i + 5]
    rest = toks[i + 5:]                           # [n] GL[ GL2 GL3] flag
    n = None
    if rest and RE_N.match(rest[0]):
        n = rest[0]
        rest = rest[1:]
    flag = rest[-1] if rest else ''
    gls = rest[:-1]
    return Rec(up=up, dn=dn, via=via, L=L, area=area, coef=coef, ilu=ilu, ild=ild,
               n=n, gls=gls, flag=flag)


def split_by_flag(toks):
    """탭이 구분자 역할을 못 하는 방언에서 플래그('0'/'1') 위치로 머리/꼬리를 나눈다.
    - TSV 방언(엑셀 저장본): 탭 전부가 구분자
    - 무탭 방언(한 설계사 납품본): 탭이 아예 없고 전부 공백 구분
    필드 순서는 동일하다: up dn L area coef ILu ILd [n] GL[ GL2 GL3] flag | D ...
    """
    for k in range(7, min(len(toks), 14)):
        if toks[k] in ('0', '1'):
            return toks[:k + 1], toks[k + 1:]
    return toks, []


split_tsv = split_by_flag          # 옛 이름 호환


def read_flo(path):
    b = open(path, 'rb').read()
    crlf = b'\r\n' in b
    lines = b.replace(b'\r\n', b'\n').split(b'\n')
    while lines and not lines[-1].strip():
        lines.pop()
    txt = [l.decode(ENC, 'replace') for l in lines]
    body = [l for l in txt[2:] if l.strip()]
    tsv = bool(body) and sum(1 for l in body if l.count('\t') >= 5) > len(body) / 2
    recs = []
    for i, l in enumerate(txt[2:], start=3):
        if not l.strip():
            continue
        if tsv:
            toks = [t.strip() for t in l.split('\t') if t.strip()]
            ht, tt = split_by_flag(toks)
        elif '\t' not in l:
            # 무탭 방언 — 플래그 위치로 가른다
            ht, tt = split_by_flag(l.split())
            if not tt:
                recs.append(Rec(_line=i, _raw=l, _err='TAB 없고 플래그(0/1)도 못 찾음'))
                continue
        else:
            head, tail = l.split('\t', 1)
            ht, tt = head.split(), tail.split()
        if len(ht) < 9:
            recs.append(Rec(_line=i, _raw=l, _err='머리 토큰 %d개(9 미만)' % len(ht)))
            continue
        r = parse_head(ht)
        r['_line'] = i
        r['_raw'] = l
        r['D'] = tt[0] if tt else None
        r['tail'] = tt[1:]
        r['Q'] = next((t[2:] for t in tt if t.startswith('Q=')), None)
        recs.append(r)
    return dict(crlf=crlf, header=txt[0] if txt else '', param=txt[1] if len(txt) > 1 else '',
                recs=recs, raw=b, path=path, tsv=tsv)


def validate(F):
    """([(심각도, 행, 메시지)], 방류구목록). ERROR = 실행 실패 가능, WARN = 관행 이탈."""
    out = []
    try:
        F['raw'].decode(ENC)
    except Exception as e:
        # 실무 납품본 26파일도 주석 필드 바이트가 깨진 채 정상 실행됐다 → 경고로만 둔다.
        out.append(('WARN', 0, 'CP949 디코드 실패(주석 필드 추정) — %s' % e))
    if F['crlf']:
        # 실무 파일에도 CRLF 가 2건 있다(한 기본계획 우수). 관행은 LF 이므로 경고만.
        out.append(('WARN', 0, '개행이 CRLF — 실적본은 전부 LF, 맞추는 편이 안전'))
    if len(F['param'].split()) != 9:
        out.append(('WARN', 2, '파라미터 2행 토큰 %d개(정상 9개): %r'
                    % (len(F['param'].split()), F['param'])))
    recs = F['recs']
    for r in recs:
        if r.get('_err'):
            out.append(('ERROR', r['_line'], r['_err']))
            continue
        for k in ('L', 'ilu', 'ild', 'area', 'coef'):
            try:
                float(r[k])
            except Exception:
                out.append(('ERROR', r['_line'], '%s 수치 아님: %r' % (k, r[k])))
        for gl in r['gls']:
            try:
                float(gl)
            except Exception:
                out.append(('ERROR', r['_line'], '지반고 수치 아님: %r' % gl))
        if not 1 <= len(r['gls']) <= 3:
            out.append(('WARN', r['_line'], '지반고 토큰 %d개(정상 1~3)' % len(r['gls'])))
        # 관경: 원형 '200'(mm) / '1.80'(m) / BOX '1.5 1.8 1'(폭 높이 연수) 모두 정상 표기
        dt = (r['D'] or '').split()
        if not (1 <= len(dt) <= 3 and all(RE_DIA.match(x) for x in dt)):
            out.append(('ERROR', r['_line'],
                        '관경 토큰 이상: %r (정수 mm / 소수 m / BOX "폭 높이 연수" 만 허용)' % r['D']))
        try:
            if float(r['L']) <= 0:
                out.append(('ERROR', r['_line'], '연장 %s <= 0' % r['L']))
        except Exception:
            pass
        for key in ('up', 'dn', 'via'):
            nm = r.get(key)
            if not nm:
                continue
            nb = len(nm.encode(ENC, 'replace'))
            if nb > MAX_NAME_BYTES:
                out.append(('ERROR', r['_line'], '%s 맨홀명 %d바이트(실적상한 %d): %s'
                            % (key, nb, MAX_NAME_BYTES, nm)))
            if nm.startswith('방류') and not RE_BANGRYU_OK.match(nm):
                # 2026-09-28 DHSSPipe 임포트 실측: '방류-처리장' 도 오류 없이 읽힌다 → 실행 실패 원인이 아님.
                # 실적본 관행은 '방류' 또는 '방류[숫자]-숫자' 뿐이므로 권고 수준으로만 남긴다.
                out.append(('WARN', r['_line'],
                            "방류 접두 관행 이탈: %r — 실적본은 '방류' 또는 '방류[숫자]-숫자'뿐. "
                            "한글 말단명은 방류 접두 없이 쓰는 편이 안전(예 '가나(다라)처리시설')" % nm))

    # ---- 위상 ----
    E = [(r['up'], r['dn']) for r in recs if not r.get('_err')]
    od = collections.Counter(u for u, v in E)
    lineof = {}
    for r in recs:
        if not r.get('_err'):
            lineof.setdefault(r['up'], r['_line'])
    for u, c in sorted(od.items()):
        if c > 1:
            out.append(('ERROR', lineof.get(u, 0),
                        '맨홀 %s 에 하류관 %d개 — 하수관망은 맨홀당 하류관 1개' % (u, c)))
    for r in recs:
        if not r.get('_err') and r['up'] == r['dn']:
            out.append(('ERROR', r['_line'], '자기루프 %s' % r['up']))
    for e, c in collections.Counter(E).items():
        if c > 1:
            out.append(('ERROR', 0, '중복간선 %s -> %s %d회' % (e[0], e[1], c)))

    nxt = collections.defaultdict(list)
    for u, v in E:
        nxt[u].append(v)
    nodes = {x for e in E for x in e}
    color = {}
    for s in nodes:
        if color.get(s) is not None:
            continue
        stack = [(s, iter(nxt[s]))]
        color[s] = 1
        while stack:
            n, it = stack[-1]
            nxt_node = next(it, None)
            if nxt_node is None:
                color[n] = 2
                stack.pop()
            elif color.get(nxt_node) == 1:
                out.append(('ERROR', 0, '순환 발견 …%s -> %s' % (n, nxt_node)))
            elif color.get(nxt_node) is None:
                color[nxt_node] = 1
                stack.append((nxt_node, iter(nxt[nxt_node])))

    outf = sorted(n for n in nodes if od[n] == 0)
    if not outf:
        out.append(('ERROR', 0, '방류구(말단 노드) 없음 — 전 노드가 하류관을 가짐'))
    pos = {}
    for i, (u, v) in enumerate(E):
        pos.setdefault(u, i)
    rev = sum(1 for i, (u, v) in enumerate(E) if v in pos and pos[v] < i)
    if rev:
        out.append(('WARN', 0, '하류가 먼저 등장한 간선 %d건 — 위상순 정렬 권고' % rev))

    sev = {'ERROR': 0, 'WARN': 1}
    return sorted(out, key=lambda x: (sev[x[0]], x[1])), outf


# ---------------------------------------------------------------- writer
# 필드는 전부 '하류맨홀 끝(=e)' 기준 상대 바이트 오프셋에 우측정렬된다. 실적본 실측.
# makesw 는 토큰 기반이라 이 서식은 '사람이 읽기 위한' 것 — 폭이 넘쳐도 파싱은 된다.
FIXED_WIDTH = dict(
    up_pad=11,
    rel=dict(L=15, area=24, coef=29, ilu=37, ild=44, n=51, gl=58, gl2=65, gl3=72, flag=78),
    d_end=83,        # 관경 우측끝 (e 기준). 탭은 e+78 에 놓인다.
    total=165,       # 표준 레코드 줄 길이 = e + total
    first_start=128, # 첫 레코드의 파라미터+Q 블록 시작 (e 기준)
)
# 첫 레코드 꼬리에 붙는 계산 파라미터 (실적본 공통 실측값)
FIRST_TAIL = '300.0       10.0   1.0       100.0 30.0'


def _place(buf, end, tok):
    """tok 의 마지막 바이트가 buf[end-1] 이 되도록 우측정렬 배치"""
    b = tok.encode(ENC)
    s = end - len(b)
    if s < 0:
        s = 0
    if end > len(buf):
        buf.extend(b' ' * (end - len(buf)))
    buf[s:end] = b


def format_record(r, first=False, dialect=FIXED_WIDTH):
    """레코드 dict → .flo 한 줄(str).
    필요한 키: up, dn, L, ilu, ild, D. 선택: gls(리스트) 또는 gl, n, Q, area, coef, flag, note"""
    rel = dialect['rel']
    up_b = r['up'].encode(ENC)
    pad = dialect['up_pad']
    head = up_b + b' ' * max(1, pad - len(up_b)) if len(up_b) < pad else up_b + b' '
    dn_b = r['dn'].encode(ENC)
    e = len(head) + len(dn_b)
    buf = bytearray(b' ' * (e + dialect['total']))
    buf[0:len(head)] = head
    buf[len(head):e] = dn_b

    _place(buf, e + rel['L'], '%.1f' % float(r['L']))
    _place(buf, e + rel['area'], '%.4f' % float(r.get('area', 0.0)))
    _place(buf, e + rel['coef'], '%.2f' % float(r.get('coef', 0.0)))
    _place(buf, e + rel['ilu'], '%.2f' % float(r['ilu']))
    _place(buf, e + rel['ild'], '%.2f' % float(r['ild']))
    if r.get('n'):                                  # 조도는 첫 레코드에만 (sticky)
        _place(buf, e + rel['n'], '%.3f' % float(r['n']))
    gls = r.get('gls') or ([r['gl']] if r.get('gl') is not None else [])
    for k, key in enumerate(('gl', 'gl2', 'gl3')):
        if k < len(gls):
            _place(buf, e + rel[key], '%.2f' % float(gls[k]))
    _place(buf, e + rel['flag'], str(r.get('flag', 0)))
    buf[e + rel['flag']:e + rel['flag'] + 1] = b'\t'
    _place(buf, e + dialect['d_end'], str(r['D']))

    q = r.get('Q')
    chunk = 'Q=' + (_fmtq(q) if q else '0')
    if r.get('n_tail'):
        chunk += ' n=' + str(r['n_tail'])
    for note in (r.get('notes') or []):
        chunk += ' ' + note
    chunk += '  '
    if first:
        # 첫 레코드는 계산 파라미터가 붙고, 블록이 e+128 에서 시작한다(줄 길이는 그만큼 늘어남)
        chunk = FIRST_TAIL + '   ' + chunk
        _place(buf, e + dialect['first_start'] + len(chunk.encode(ENC)), chunk)
    else:
        _place(buf, e + dialect['total'], chunk)
    return buf.decode(ENC)


def _fmtq(q):
    """유량(㎥/s)을 실적본 표기대로 — 지수 없이, 뒤 0 제거"""
    s = ('%.8f' % float(q)).rstrip('0')
    return s if s and s != '0.' else '0'


def write_flo(path, header, param, recs, dialect=FIXED_WIDTH, n=None, validate_first=True):
    """레코드 목록 → .flo 파일. 쓰기 전에 검증해 ERROR 가 있으면 예외.
    recs 는 위상순(상류 먼저)으로 미리 정렬해 넘길 것."""
    lines = [header, param]
    for i, r in enumerate(recs):
        rr = dict(r)
        rr['n'] = n if (i == 0 and n) else None      # 조도는 첫 줄만
        rr.setdefault('n_tail', n if n else None)
        lines.append(format_record(rr, first=(i == 0), dialect=dialect))
    data = (NL.join(lines) + NL).encode(ENC)         # errors 지정 안 함 — 인코딩 불가 문자는 즉시 실패
    if validate_first:
        import tempfile
        tf = tempfile.NamedTemporaryFile('wb', suffix='.flo', delete=False)
        tf.write(data)
        tf.close()
        F = read_flo(tf.name)
        f, _ = validate(F)
        os.unlink(tf.name)
        errs = [(ln, m) for s, ln, m in f if s == 'ERROR']
        if errs:
            raise ValueError('생성물 검증 실패 %d건: %s' % (len(errs), errs[:3]))
    open(path, 'wb').write(data)
    return len(recs)


def topo_sort(recs):
    """상류가 먼저 오도록 위상 정렬 (동순위는 하류맨홀 이름순)"""
    import heapq
    nodes = {x for r in recs for x in (r['up'], r['dn'])}
    down = collections.defaultdict(list)
    indeg = collections.Counter(r['dn'] for r in recs)
    for i, r in enumerate(recs):
        down[r['up']].append(i)
    ready = [n for n in nodes if indeg[n] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        nd = heapq.heappop(ready)
        for i in sorted(down[nd], key=lambda j: recs[j]['dn']):
            order.append(i)
            indeg[recs[i]['dn']] -= 1
            if indeg[recs[i]['dn']] == 0:
                heapq.heappush(ready, recs[i]['dn'])
    if len(order) != len(recs):
        raise ValueError('위상 정렬 실패 — 순환 또는 고립 간선 (정렬 %d / 전체 %d)' % (len(order), len(recs)))
    return [recs[i] for i in order]


def fix_outfall(F, mapping):
    """방류 명명위반 노드를 mapping 으로 치환한 텍스트 반환 (머리 블록만 손댐)"""
    lines = F['raw'].replace(b'\r\n', b'\n').decode(ENC).split(NL)
    out = lines[:2]
    for l in lines[2:]:
        if '\t' in l:
            head, tail = l.split('\t', 1)
            for a, b2 in mapping.items():
                rep = b2.ljust(len(a)) if len(b2) <= len(a) else b2
                head = re.sub(r'(^|(?<= ))' + re.escape(a) + r'(?=($| ))', rep, head)
            l = head + '\t' + tail
        out.append(l)
    return NL.join(out)


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if '--fix-outfall' in argv:
        argv = [a for a in argv if a != '--fix-outfall']
        for p in argv:
            F = read_flo(p)
            _, outf = validate(F)
            bad = [n for n in outf if n.startswith('방류') and not RE_BANGRYU_OK.match(n)]
            if not bad:
                print('%s: 방류 명명위반 없음 — 수정 불필요' % os.path.basename(p))
                continue
            mp = {n: '방류-%d' % (100 + 2 * i) for i, n in enumerate(bad)}
            q = p[:-4] + '_fixed' + p[-4:]
            open(q, 'wb').write(fix_outfall(F, mp).encode(ENC))
            print('%s -> %s   치환 %s' % (os.path.basename(p), os.path.basename(q), mp))
        return 0
    rc = 0
    for p in argv:
        F = read_flo(p)
        f, outf = validate(F)
        ne = sum(1 for s, _, _ in f if s == 'ERROR')
        print('======== %s' % os.path.basename(p))
        print('  레코드 %d / 방류구 %s' % (len(F['recs']), outf))
        print('  판정: %s  (ERROR %d, WARN %d)' % ('통과' if ne == 0 else '*** 실패 ***', ne, len(f) - ne))
        shown = collections.Counter()
        for s, ln, m in f:
            k = m[:40]
            shown[k] += 1
            if shown[k] <= 3:
                print('   [%-5s] %s%s' % (s, ('%d행 ' % ln) if ln else '', m))
        more = {k: c for k, c in shown.items() if c > 3}
        if more:
            print('   (동일유형 생략: %s)' % ', '.join('%s… x%d' % (k[:26], c) for k, c in more.items()))
        print()
        if ne:
            rc = 1
    return rc


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main(sys.argv[1:]))
