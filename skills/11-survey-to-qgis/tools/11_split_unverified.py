# -*- coding: utf-8 -*-
"""11 — 근거가 약한 폴리곤을 지장물 레이어에서 '확인필요' 레이어로 분리.

왜
  빌더가 폴리곤을 만드는 경로는 근거의 세기가 다르다.
    폐합(도면)      : 도면이 직접 닫아놨다            → 강함
    체인쌍(평행 2선): 관벽 두 선 간격 = 관경          → 강함
    단선버퍼/추정   : 근거가 "그 선이 배수시설물 레이어에 있다"뿐 → 약함
  약한 것을 같은 레이어에 섞어 두면 경계선·구조물선이 지장물로 올라간다
  (사례: 두 건 = 교량 옆 강·도로 경계선이 암거 라벨까지 상속받아 있었다).

규칙
  단선버퍼·추정 → `확인필요` 레이어로 이동.
  단, 아래는 검증된 것으로 보아 지장물 레이어에 남긴다.
    ① 설계사 종단(.flo x= 토큰)과 일치 — 06_check_profile 결과
       (사각 토큰 X= 는 크기 규약이 달라 규격 비교를 생략하므로 '위치일치' 도 검증으로 본다)
    ② 보정 기록(보정_YYMMDD / fields.obstacle_poly.fix_note)에 종단·말풍선·사용자 확인 근거가 있다
       (ㄷ자 모양 보정만 적힌 것은 '모양을 고쳤다'일 뿐 지장물 여부 검증이 아니므로 제외)
    ③ 규격 텍스트가 이 폴리곤 안의 선에 0.6 m 이내로 '밀착' 해 있다
       — 도면이 그 선에 규격을 직접 적어놓은 것이므로 지장물 근거로 충분하다.
       (그중 한 건은 라벨이 7.8 m 떨어진 암거 것이 전파된 경우였다)

사용 (공통: [--config sewer.config.json] [--gpkg ...] [--poly ...] [--cl ...] [--src <경량.dxf>])
  python -X utf8 11_split_unverified.py [--verified <06 결과.csv>] [--out <확인필요.gpkg>]
        [--district ...] [--srcgpkg <빌드.gpkg>] [--write]
  python -X utf8 11_split_unverified.py --promote [--verified <산출>/06_확인필요_check.csv] [--write]
  --verified 기본: 분리 = <산출>/06_profile_check.csv, --promote = <산출>/06_확인필요_check.csv
  --out(확인필요 GPKG) 기본: layers.obstacle_review 의 GPKG, 없으면 <산출>/지장물_확인필요.gpkg
  --write 없으면 집계만. 이동분은 원 레이어에서 삭제되고 확인필요 레이어에 들어간다.
"""
import os
import sys
import csv
import datetime
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

GPKG, POLY, CL = obstacle_src(review=False)
VER = argval('--verified', out_path('06_확인필요_check.csv' if has('--promote')
                                    else '06_profile_check.csv'))
_RG, REV_POLY, REV_CL = review_names()
OUT = argval('--out') or _RG or out_path('지장물_확인필요.gpkg')
SRCG = argval('--srcgpkg')
SRC = light_dxf()
NEAR = float(argval('--near', '0.6'))
WEAK = ('단선버퍼', '추정')
STAMP = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
NOTE_W = note_field()


def main():
    MM, _ = method_map(SRCG)
    rows = read_layer(GPKG, POLY)
    NOTES = note_fields(columns(GPKG, POLY))
    cls = {pr.get('지장물번호'): (pr, g) for pr, g in read_layer(GPKG, CL)}
    cur = route_lines(current=True)
    ctree = STRtree([g for _, g in cur])

    # 종단으로 검증된 번호
    verified = set()
    try:
        for r in csv.DictReader(open(VER, encoding='utf-8-sig')):
            if r.get('판정', '').startswith(('일치', '위치일치')) and r.get('폴리곤'):
                verified.add(r['폴리곤'].strip())
        print('종단 검증 목록 %s → %d개' % (os.path.basename(VER), len(verified)))
    except Exception as e:
        print('종단 검증 목록 없음(%s) — ② 근거만 적용' % e)

    # ③ 규격 텍스트가 폴리곤 안의 선에 밀착한 것 추리기
    doc = load_dxf(SRC)
    ch = chains(dxf_lines(doc, PIPE_LAYERS))
    ctree_l = STRtree(ch)
    specs = [(x, y, parse_spec(t), t) for lay, t, x, y in dxf_texts(doc) if parse_spec(t)]
    stree = STRtree([Point(x, y) for x, y, _, _ in specs])
    attached = set()
    for pr, g in rows:
        own = [ch[int(i)] for i in ctree_l.query(g.buffer(0.1))
               if ch[int(i)].intersects(g.buffer(0.05))]
        if not own:
            continue
        mine = specs_of_row(pr)
        for i in [int(x) for x in stree.query(g.buffer(NEAR + 1.0))]:
            P = Point(specs[i][0], specs[i][1])
            if min(c.distance(P) for c in own) <= NEAR and                     (not mine or any(spec_eq(specs[i][2], m) for m in mine)):
                attached.add(str(pr.get('지장물번호')))
                break
    print('규격 텍스트가 자기 선에 밀착한 폴리곤 %d개' % len(attached))

    move, keep_v, cnt = [], [], collections.Counter()
    for pr, g in rows:
        k = str(pr.get('지장물번호'))
        m = str(pr.get('관경산출') or MM.get(k) or '')
        weak = any(w in m for w in WEAK)
        cnt['전체'] += 1
        if not weak:
            continue
        cnt['약한근거'] += 1
        why = []
        if k in verified:
            why.append('종단 규격 일치')
        note = note_text(pr, NOTES)
        if any(t in note for t in ('종단', '말풍선', '사용자 확인')):
            why.append('보정 근거 기록(종단·말풍선·확인)')
        if k in attached:
            why.append('도면 라벨 밀착')
        if why:
            keep_v.append((k, m, ' / '.join(why)))
            continue
        d, kc = None, None
        for i in [int(x) for x in ctree.query(g.buffer(40))]:
            dd = cur[i][1].distance(g)
            if d is None or dd < d:
                d, kc = dd, cur[i][0]
        L, w = band_dims(g)
        move.append((pr, g, m, round(d, 1) if d is not None else None, kc,
                     round(w, 3) if w else None))

    print('\n폴리곤 %d / 약한 근거(단선버퍼·추정) %d' % (cnt['전체'], cnt['약한근거']))
    print('  → 검증돼 지장물에 남김 %d개' % len(keep_v))
    for k, m, why in keep_v[:30]:
        print('      %-12s %-22s %s' % (k, m, why))
    print('  → 확인필요로 이동 %d개' % len(move))
    near = [x for x in move if x[3] is not None and x[3] <= 10]
    print('     그중 금회 노선 10 m 이내 %d개 / 면적 중앙 %.2f ㎡' % (
        len(near), sorted(x[1].area for x in move)[len(move) // 2] if move else 0))
    print('  이동 후 지장물 레이어: %d → %d개' % (cnt['전체'], cnt['전체'] - len(move)))

    print()
    print('[확인필요로 이동 — 금회 노선 10 m 이내]')
    for pr, g, m, d, kc, w in sorted((x for x in move if x[3] is not None and x[3] <= 10),
                                     key=lambda r: -r[1].area):
        print('   %-11s %-9s %-13s %7.2f m2 폭 %-6s %-22s %s %.1fm' % (
            pr.get('지장물번호'), pr.get('종류'), pr.get('표기') or '-', g.area,
            w, m, kc or '-', d))

    if not has('--write'):
        print('\n(쓰기 안 함 — 반영은 --write)')
        return

    backup(GPKG)
    schema_p = {'geometry': 'Polygon', 'properties': dict([
        ('지장물번호', 'str'), ('종류', 'str'), ('표기', 'str'), ('규격_폭m', 'float'),
        ('규격_깊이m', 'float'), ('관경mm', 'float'), ('구분', 'str'), ('실측폭m', 'float'),
        ('면적m2', 'float'), ('생성방식', 'str'), ('분리사유', 'str'), ('분리일시', 'str'),
        ('금회노선거리m', 'float'), ('금회관번호', 'str')])}
    schema_c = dict(schema_p)
    schema_c['geometry'] = 'LineString'

    pf, cf = [], []
    for pr, g, m, d, kc, w in move:
        p = {'지장물번호': pr.get('지장물번호'), '종류': pr.get('종류'), '표기': pr.get('표기'),
             '규격_폭m': pr.get('규격_폭m'), '규격_깊이m': pr.get('규격_깊이m'),
             '관경mm': pr.get('관경mm'), '구분': pr.get('구분'), '실측폭m': w,
             '면적m2': round(g.area, 2), '생성방식': m,
             '분리사유': '단선버퍼·기본폭 추정 — 지장물 근거가 레이어뿐이고 종단·원도 검증 없음',
             '분리일시': STAMP, '금회노선거리m': d, '금회관번호': kc}
        pf.append((g, p))
        if pr.get('지장물번호') in cls:
            cf.append((cls[pr.get('지장물번호')][1], p))

    # 한 GPKG 에 레이어 둘을 쓸 때 fiona 의 mode 'a' 는 새 레이어에 쓰면 NULL pointer 로 죽는다.
    # 02_build_poly 와 같은 방식(geopandas.to_file, 레이어별 호출)으로 쓴다.
    import geopandas as gpd
    cols = list(schema_p['properties'])
    gpd.GeoDataFrame([p for _, p in pf], geometry=[g for g, _ in pf],
                     crs='EPSG:%d' % epsg())[cols + ['geometry']].to_file(
        OUT, layer=REV_POLY, driver='GPKG')
    if cf:
        gpd.GeoDataFrame([p for _, p in cf], geometry=[g for g, _ in cf],
                         crs='EPSG:%d' % epsg())[cols + ['geometry']].to_file(
            OUT, layer=REV_CL, driver='GPKG')
    print('확인필요 레이어 생성: %s | %s · %s (폴리곤 %d / 중심선 %d)' % (
        OUT, REV_POLY, REV_CL, len(pf), len(cf)))

    con = sqlite3.connect(GPKG)
    c = con.cursor()
    np_ = nc = 0
    for pr, g, m, d, kc, w in move:
        c.execute('delete from "%s" where 지장물번호=?' % POLY, (pr.get('지장물번호'),))
        np_ += c.rowcount
        try:
            c.execute('delete from "%s" where 지장물번호=?' % CL, (pr.get('지장물번호'),))
            nc += c.rowcount
        except Exception:
            pass
    con.commit()
    con.close()
    print('원 레이어에서 삭제: 폴리곤 %d / 중심선 %d' % (np_, nc))
    feature_counts(GPKG, [POLY, CL])


def promote():
    """--promote : 확인필요 레이어에서 검증된 것만 지장물 레이어로 되돌린다.
       근거 ① 설계사 종단과 규격 일치  ③ 규격 텍스트가 자기 선에 밀착
       (빌더 02 가 단선버퍼·추정을 확인필요로 내보낸 뒤 이 단계를 돌린다)"""
    src_g = argval('--from-gpkg', OUT)
    src_p = argval('--from-poly', REV_POLY)
    src_c = argval('--from-cl', REV_CL)
    rows = read_layer(src_g, src_p)
    try:
        clrows = {pr.get('지장물번호'): (pr, g) for pr, g in read_layer(src_g, src_c)}
    except Exception:
        clrows = {}
    print('확인필요 %s | %s : %d개' % (os.path.basename(src_g), src_p, len(rows)))

    verified = set()
    try:
        for r in csv.DictReader(open(VER, encoding='utf-8-sig')):
            if r.get('판정', '').startswith(('일치', '위치일치')) and r.get('폴리곤'):
                verified.add(r['폴리곤'].strip())
    except Exception:
        pass
    doc = load_dxf(SRC)
    ch = chains(dxf_lines(doc, PIPE_LAYERS))
    ctree_l = STRtree(ch)
    specs = [(x, y, parse_spec(t), t) for lay, t, x, y in dxf_texts(doc) if parse_spec(t)]
    stree = STRtree([Point(x, y) for x, y, _, _ in specs])

    ok = []
    for pr, g in rows:
        k = str(pr.get('지장물번호'))
        why = []
        if k in verified:
            why.append('종단 규격 일치')
        own = [ch[int(i)] for i in ctree_l.query(g.buffer(0.1))
               if ch[int(i)].intersects(g.buffer(0.05))]
        if own:
            mine = specs_of_row(pr)
            for i in [int(x) for x in stree.query(g.buffer(NEAR + 1.0))]:
                P = Point(specs[i][0], specs[i][1])
                if min(c.distance(P) for c in own) <= NEAR and \
                        (not mine or any(spec_eq(specs[i][2], m) for m in mine)):
                    why.append('도면 라벨 밀착')
                    break
        if why:
            ok.append((pr, g, ' / '.join(why)))
    print('되돌릴 대상 %d개' % len(ok))
    for pr, g, why in ok[:30]:
        print('   %-12s %-9s %-13s %8.2f m2  %s' % (
            pr.get('지장물번호'), pr.get('종류'), pr.get('표기') or '-', g.area, why))
    if not has('--write') or not ok:
        print('\n(쓰기 안 함 — 반영은 --write)' if not has('--write') else '')
        return

    backup(GPKG)
    add_column(GPKG, POLY, NOTE_W)
    pf = []
    cf = []
    for pr, g, why in ok:
        p = dict(pr)
        p[NOTE_W] = ('확인필요 → 지장물 승격: %s [11_split_unverified --promote]' % why)
        pf.append((g, p))
        if pr.get('지장물번호') in clrows:
            cf.append((clrows[pr.get('지장물번호')][1], p))
    print('지장물 레이어에 추가 %d' % append_features(GPKG, POLY, pf))
    if cf:
        try:
            print('중심선에 추가 %d' % append_features(GPKG, CL, cf))
        except Exception as e:
            print('중심선 추가 실패:', e)
    con = sqlite3.connect(src_g)
    c = con.cursor()
    n = 0
    for pr, g, why in ok:
        c.execute('delete from "%s" where 지장물번호=?' % src_p, (pr.get('지장물번호'),))
        n += c.rowcount
        try:
            c.execute('delete from "%s" where 지장물번호=?' % src_c, (pr.get('지장물번호'),))
        except Exception:
            pass
    con.commit()
    con.close()
    print('확인필요에서 제거 %d' % n)
    feature_counts(GPKG, [POLY, CL])


if __name__ == '__main__':
    promote() if has('--promote') else main()
