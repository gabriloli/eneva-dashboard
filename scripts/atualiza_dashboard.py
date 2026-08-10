#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualiza o dashboard de debentures da Eneva a partir da API REST do Credit Guide.
Roda no GitHub Actions. Nao depende de conector MCP nem de sessao interativa.

Variaveis de ambiente:
  CG_API_KEY   (obrigatoria) chave estatica da API do Credit Guide
  CG_BASE_URL  (opcional) default https://api.creditguide.com.br
  CG_AUTH_MODE (opcional) 'bearer' (default) ou 'apikey'
  TARGET       (opcional) caminho do html, default index.html
"""
import json, os, re, sys, time, urllib.request, urllib.error

BASE   = os.environ.get('CG_BASE_URL', 'https://api.creditguide.com.br').rstrip('/')
KEY    = os.environ.get('CG_API_KEY', '')
MODE   = os.environ.get('CG_AUTH_MODE', 'bearer').lower()
TARGET = os.environ.get('TARGET', 'index.html')

CODES = ['CESE22','CESE32','ENEV13','ENEV15','ENEV16','ENEV18','ENEV19','ENEV26',
         'ENEV28','ENEV29','ENEV32','ENEV38','ENEV39','ENEV48','ENEVA0','ENEVA3',
         'ENEVA4','ENEVB0','ENEVB4','ENEVC0','ENEVD0']

# ordem em que o bloco NTNB_RATE e reescrito (apenas series IPCA)
NTNB_ORDER = ['CESE32','ENEV13','ENEV15','ENEV16','ENEV18','ENEV19','ENEV26','ENEV28',
              'ENEV29','ENEV32','ENEV39','ENEVA0','ENEVA4','ENEVB0','ENEVB4']

# marcadores que precisam sobreviver: se sumirem, o script aborta sem publicar
GUARDS = ['id="rtChart"', 'id="dtChart"', 'sec-num">12', 'sec-num">13',
          'sec-num">11', 'const SECTOR_SPREAD', 'noindex']


def log(msg):
    print(msg, flush=True)


def headers():
    if not KEY:
        sys.exit('ERRO: CG_API_KEY nao definida nos secrets do repositorio.')
    h = {'Accept': 'application/json', 'User-Agent': 'eneva-dashboard-bot'}
    h['X-API-Key' if MODE == 'apikey' else 'Authorization'] = KEY if MODE == 'apikey' else 'Bearer ' + KEY
    return h


def get(path, tries=4):
    url = BASE + path
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers())
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            last = 'HTTP %s em %s: %s' % (e.code, path, e.read()[:300].decode('utf-8', 'replace'))
            if e.code in (401, 403):
                sys.exit('ERRO DE AUTENTICACAO. ' + last)
            if e.code not in (429, 500, 502, 503, 504):
                sys.exit('ERRO: ' + last)
        except Exception as e:
            last = '%s em %s' % (e, path)
        time.sleep(3 * (i + 1))
    sys.exit('ERRO apos %d tentativas: %s' % (tries, last))


def fetch_all():
    out, as_of = {}, set()
    for c in CODES:
        d = get('/v1/debentures/%s/market' % c)
        d = d.get('data', d)
        out[c] = d
        if d.get('as_of'):
            as_of.add(d['as_of'])
        log('  %-7s as_of=%s' % (c, d.get('as_of')))
    if len(as_of) != 1:
        log('AVISO: datas as_of divergentes entre as series: %s' % sorted(as_of))
    return out, sorted(as_of)[-1]


def r2(x):
    return round(float(x), 2)


def build_row(d):
    px, rt, sp = d['price'], d['rates'], d['spreads']
    ix, vol, tm = d['indexation'], d['volume'], d['term_metrics']
    pu_par = r2(px['par_price'] * 100)
    return dict(
        duration=r2(tm['duration']), ytm=r2(rt['indicative_rate']),
        spreadOver=r2(sp['spread_over'] / 100.0), compra=r2(rt['bid_rate']),
        venda=r2(rt['ask_rate']), pu=r2(px['unit_price']), puPar=pu_par,
        pctPuPar=r2(px['unit_price'] / pu_par * 100.0),
        volMed20=r2(vol['average_20d'] / 1000.0), volDia=r2(vol['traded']),
        var1=r2(sp['spread_var_1d'] / 100.0), var7=r2(sp['spread_var_7d'] / 100.0),
        var30=r2(sp['spread_var_30d'] / 100.0),
        spreadEqIR=r2(sp['spread_over_tax_gross_up'] / 100.0),
        prazoDias=float(round(tm['remaining_term'] * 252)),
        qtdMercado=float(d['market_quantity']['value']),
    ), ix.get('reference_rate')


def br(d):
    return '%s/%s/%s' % (d[8:10], d[5:7], d[0:4])


def main():
    html = open(TARGET, encoding='utf-8').read()
    for g in GUARDS:
        if g not in html:
            sys.exit('ERRO: marcador ausente no arquivo de entrada: %s' % g)

    cur = re.search(r"const SNAP_REF = '([^']*)';", html).group(1)
    log('Arquivo atual: SNAP_REF = %s' % cur)

    log('Buscando %d series no Credit Guide...' % len(CODES))
    data, as_of = fetch_all()
    novo = br(as_of)
    if novo == cur:
        log('Sem pregao novo (as_of %s ja publicado). Nada a fazer.' % novo)
        print('::notice::sem-dado-novo')
        return 0

    m = re.search(r'const SNAPSHOT = (\[.*?\]);\n', html, re.S)
    arr = json.loads(m.group(1))
    if len(arr) != len(CODES) or {a['codigo'] for a in arr} != set(CODES):
        sys.exit('ERRO: SNAPSHOT do arquivo nao bate com a lista de codigos.')

    ntnb, chg = {}, 0
    for a in arr:
        row, ref = build_row(data[a['codigo']])
        if ref is not None:
            ntnb[a['codigo']] = r2(ref)
        for k, v in row.items():
            if a.get(k) != v:
                chg += 1
            a[k] = v

    html = html[:m.start(1)] + json.dumps(arr, ensure_ascii=False, separators=(', ', ': ')) + html[m.end(1):]

    if set(ntnb) != set(NTNB_ORDER):
        sys.exit('ERRO: series IPCA retornadas (%s) diferem do esperado.' % sorted(ntnb))
    linhas = ['  ' + ', '.join('%s: %g' % (c, ntnb[c]) for c in NTNB_ORDER[i:i + 5]) + ','
              for i in range(0, 15, 5)]
    linhas[-1] = linhas[-1].rstrip(',')
    html = re.sub(r'const NTNB_RATE = \{.*?\n\};',
                  'const NTNB_RATE = {\n' + '\n'.join(linhas) + '\n};', html, count=1, flags=re.S)

    for a, b in [("const SNAP_REF = '%s';" % cur, "const SNAP_REF = '%s';" % novo),
                 ('Snapshot local · ' + cur, 'Snapshot local · ' + novo),
                 ('base Credit Guide (%s)' % cur, 'base Credit Guide (%s)' % novo),
                 ('fonte Credit Guide ' + cur, 'fonte Credit Guide ' + novo),
                 ('fechamento de ' + cur, 'fechamento de ' + novo)]:
        html = html.replace(a, b)

    for g in GUARDS:
        if g not in html:
            sys.exit('ERRO: o marcador %s desapareceu apos a edicao. Nada foi gravado.' % g)
    if "const SNAP_REF = '%s';" % novo not in html:
        sys.exit('ERRO: SNAP_REF nao foi atualizado.')

    open(TARGET, 'w', encoding='utf-8').write(html)
    log('OK: %s -> %s (%d campos alterados)' % (cur, novo, chg))
    print('::notice::atualizado para %s' % novo)
    with open(os.environ.get('GITHUB_OUTPUT', '/dev/null'), 'a') as f:
        f.write('ref=%s\n' % novo)
    return 0


if __name__ == '__main__':
    sys.exit(main())
