#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genere un site statique de classement momentum pour ETF eligibles au PEA.

Usage:
    python3 update.py            # recupere les cours et genere public/index.html
    python3 update.py --demo     # genere une page de demonstration (donnees fictives)

Aucune dependance externe : uniquement la bibliotheque standard Python.
"""

import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, date, timedelta
from pathlib import Path

RACINE = Path(__file__).parent
FICHIER_ETFS = RACINE / "etfs.json"
CACHE_SYMBOLES = RACINE / "symboles_cache.json"
DOSSIER_SORTIE = RACINE / "public"

# Ponderation du score composite (doit totaliser 100)
POIDS_3M = 50
POIDS_6M = 50

# Nombre de lignes retenues pour l'investissement
TOP_N = 2

UA = "Mozilla/5.0 (compatible; momentum-pea/1.0)"

# Nombre de seances de bourse approximatif par periode
SEANCES = {"3M": 63, "6M": 126}


# --------------------------------------------------------------------------
# Recuperation des donnees
# --------------------------------------------------------------------------

def _requete_json(url, tentatives=3):
    """GET JSON avec retries et backoff."""
    derniere_erreur = None
    for i in range(tentatives):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as reponse:
                return json.loads(reponse.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            derniere_erreur = e
            if i < tentatives - 1:
                time.sleep(2 ** i)
    raise RuntimeError("echec requete %s : %s" % (url, derniere_erreur))


def charger_cache_symboles():
    if CACHE_SYMBOLES.exists():
        return json.loads(CACHE_SYMBOLES.read_text(encoding="utf-8"))
    return {}


def sauver_cache_symboles(cache):
    CACHE_SYMBOLES.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def resoudre_symbole(isin, cache):
    """
    Trouve le symbole Yahoo correspondant a un ISIN.
    On ne code pas les tickers en dur : ils varient selon la place de cotation
    et changent au fil des fusions de gammes (Lyxor -> Amundi par exemple).
    Priorite aux cotations Euronext Paris (.PA), qui sont celles de ton PEA.
    """
    if isin in cache:
        return cache[isin]

    url = "https://query2.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=10&newsCount=0" % isin
    donnees = _requete_json(url)
    resultats = donnees.get("quotes", [])
    if not resultats:
        return None

    # Priorite : Paris, puis toute autre place europeenne, puis le premier resultat
    for r in resultats:
        symbole = r.get("symbol", "")
        if symbole.endswith(".PA"):
            cache[isin] = symbole
            return symbole
    for r in resultats:
        symbole = r.get("symbol", "")
        if "." in symbole:
            cache[isin] = symbole
            return symbole

    symbole = resultats[0].get("symbol")
    cache[isin] = symbole
    return symbole


def recuperer_historique(symbole):
    """Recupere ~2 ans de cours de cloture ajustes."""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%s"
        "?range=2y&interval=1d" % urllib.parse.quote(symbole)
    )
    donnees = _requete_json(url)
    resultat = donnees["chart"]["result"][0]
    horodatages = resultat["timestamp"]
    indicateurs = resultat["indicators"]

    # Le cours ajuste tient compte des dividendes detaches : indispensable
    # pour comparer un ETF distribuant a un ETF capitalisant sans biais.
    if "adjclose" in indicateurs and indicateurs["adjclose"]:
        cours = indicateurs["adjclose"][0]["adjclose"]
    else:
        cours = indicateurs["quote"][0]["close"]

    couples = [
        (datetime.utcfromtimestamp(t).date(), c)
        for t, c in zip(horodatages, cours)
        if c is not None
    ]
    couples.sort(key=lambda x: x[0])
    return couples


# --------------------------------------------------------------------------
# Calculs
# --------------------------------------------------------------------------

def calculer_indicateurs(historique):
    """Retourne perf 3M, perf 6M, SMA200 et position par rapport a la SMA200."""
    cours = [c for _, c in historique]
    if len(cours) < 20:
        return None

    dernier = cours[-1]
    resultat = {"dernier_cours": dernier, "date_cours": historique[-1][0].isoformat()}

    for etiquette, nb_seances in SEANCES.items():
        if len(cours) > nb_seances:
            reference = cours[-1 - nb_seances]
            resultat["perf_%s" % etiquette] = (dernier / reference - 1) * 100
        else:
            resultat["perf_%s" % etiquette] = None

    if len(cours) >= 200:
        sma200 = sum(cours[-200:]) / 200
        resultat["sma200"] = sma200
        resultat["au_dessus_sma200"] = dernier > sma200
        resultat["ecart_sma200"] = (dernier / sma200 - 1) * 100
    else:
        # Pas assez d'historique : on ne devine pas, on exclut par prudence.
        resultat["sma200"] = None
        resultat["au_dessus_sma200"] = None
        resultat["ecart_sma200"] = None

    return resultat


def calculer_score(indicateurs):
    """Score composite pondere entre 3M et 6M."""
    p3, p6 = indicateurs.get("perf_3M"), indicateurs.get("perf_6M")
    total, poids = 0.0, 0
    if p3 is not None:
        total += p3 * POIDS_3M
        poids += POIDS_3M
    if p6 is not None:
        total += p6 * POIDS_6M
        poids += POIDS_6M
    return total / poids if poids else None


# --------------------------------------------------------------------------
# Calendrier de rotation
# --------------------------------------------------------------------------

def dernier_jour_ouvre(annee, mois):
    """
    Dernier jour ouvre du mois (lundi-vendredi).
    Note : tous les mois n'ont pas de 31. Fevrier, avril, juin, septembre et
    novembre n'en ont pas, donc on raisonne en 'dernier jour de bourse' plutot
    qu'en date fixe.
    """
    if mois == 12:
        jour = date(annee + 1, 1, 1) - timedelta(days=1)
    else:
        jour = date(annee, mois + 1, 1) - timedelta(days=1)
    while jour.weekday() >= 5:
        jour -= timedelta(days=1)
    return jour


def premier_jour_ouvre(annee, mois):
    jour = date(annee, mois, 1)
    while jour.weekday() >= 5:
        jour += timedelta(days=1)
    return jour


def prochaines_dates_rotation(aujourdhui=None):
    """Retourne (date_vente, date_achat) de la prochaine fenetre de rotation."""
    aujourdhui = aujourdhui or date.today()
    vente = dernier_jour_ouvre(aujourdhui.year, aujourdhui.month)
    if aujourdhui > vente:
        mois_suivant = aujourdhui.month % 12 + 1
        annee_suivante = aujourdhui.year + (1 if aujourdhui.month == 12 else 0)
        vente = dernier_jour_ouvre(annee_suivante, mois_suivant)

    mois_apres = vente.month % 12 + 1
    annee_apres = vente.year + (1 if vente.month == 12 else 0)
    achat = premier_jour_ouvre(annee_apres, mois_apres)
    return vente, achat


# --------------------------------------------------------------------------
# Generation HTML
# --------------------------------------------------------------------------

MOIS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
           "aout", "septembre", "octobre", "novembre", "decembre"]


def date_fr(d):
    jour = "1er" if d.day == 1 else str(d.day)
    return "%s %s %d" % (jour, MOIS_FR[d.month - 1], d.year)


def fmt_pct(v, decimales=1):
    if v is None:
        return "—"
    return "%+.*f%%" % (decimales, v)


def classe_signe(v):
    if v is None:
        return ""
    return "pos" if v >= 0 else "neg"


def generer_html(lignes, horodatage, demo=False):
    eligibles = [l for l in lignes if l.get("au_dessus_sma200") and l.get("score") is not None]
    exclues = [l for l in lignes if l not in eligibles]
    eligibles.sort(key=lambda l: l["score"], reverse=True)

    top = eligibles[:TOP_N]
    vente, achat = prochaines_dates_rotation()

    # Alerte de concentration : deux lignes du meme bloc geographique ne
    # diversifient pas, meme si ce sont deux ETF differents.
    alerte_concentration = ""
    if len(top) >= 2:
        blocs = {l["bloc"] for l in top}
        if len(blocs) == 1:
            alerte_concentration = (
                '<div class="alerte">Les %d lignes en tete appartiennent au meme bloc '
                "(%s). Deux ETF differents sur la meme zone ne diversifient pas : "
                "ta poche serait un pari unique.</div>" % (len(top), list(blocs)[0])
            )

    alerte_vide = ""
    if not eligibles:
        alerte_vide = (
            '<div class="alerte">Aucune ligne au-dessus de sa SMA200 : le filtre de '
            "tendance dit de rester en liquidites plutot que d'investir ce mois-ci.</div>"
        )
    elif len(eligibles) < TOP_N:
        alerte_vide = (
            '<div class="alerte">Seulement %d ligne(s) passent le filtre de tendance, '
            "pour %d emplacements. Le reste devrait rester en liquidites.</div>"
            % (len(eligibles), TOP_N)
        )

    banniere_demo = ""
    if demo:
        banniere_demo = (
            '<div class="demo">DONNEES FICTIVES — page de demonstration pour juger '
            "la mise en page. Aucun de ces chiffres n'est reel.</div>"
        )

    # --- lignes du classement ---
    html_top = ""
    for i, l in enumerate(top):
        html_top += """
        <div class="carte-top">
          <div class="rang">%d</div>
          <div class="carte-corps">
            <div class="carte-nom">%s</div>
            <div class="carte-meta">%s · ISIN %s · frais %.2f%%/an</div>
          </div>
          <div class="carte-score %s">%s</div>
        </div>""" % (
            i + 1, l["nom"], l["categorie"], l["isin"], l["frais"],
            classe_signe(l["score"]), fmt_pct(l["score"]),
        )

    html_rangs = ""
    for i, l in enumerate(eligibles):
        html_rangs += """
        <tr class="%s">
          <td class="num">%d</td>
          <td><strong>%s</strong><br><span class="petit">%s</span></td>
          <td class="num %s">%s</td>
          <td class="num %s">%s</td>
          <td class="num score %s">%s</td>
          <td class="num petit">%s</td>
        </tr>""" % (
            "retenue" if i < TOP_N else "",
            i + 1, l["nom"], l["isin"],
            classe_signe(l.get("perf_3M")), fmt_pct(l.get("perf_3M")),
            classe_signe(l.get("perf_6M")), fmt_pct(l.get("perf_6M")),
            classe_signe(l["score"]), fmt_pct(l["score"]),
            fmt_pct(l.get("ecart_sma200")),
        )

    html_exclues = ""
    for l in exclues:
        if l.get("erreur"):
            motif = "donnee indisponible (%s)" % l["erreur"]
        elif l.get("au_dessus_sma200") is None:
            motif = "historique insuffisant pour calculer la SMA200"
        else:
            motif = "sous sa SMA200"
        html_exclues += """
        <tr class="exclue">
          <td class="num">—</td>
          <td><strong>%s</strong><br><span class="petit">%s</span></td>
          <td class="num %s">%s</td>
          <td class="num %s">%s</td>
          <td class="num">—</td>
          <td class="num petit">%s</td>
        </tr>""" % (
            l["nom"], l["isin"],
            classe_signe(l.get("perf_3M")), fmt_pct(l.get("perf_3M")),
            classe_signe(l.get("perf_6M")), fmt_pct(l.get("perf_6M")),
            motif,
        )

    valeurs = {
        "horodatage": horodatage,
        "banniere_demo": banniere_demo,
        "date_vente": date_fr(vente),
        "date_achat": date_fr(achat),
        "top_n": str(TOP_N),
        "poids_3m": str(POIDS_3M),
        "poids_6m": str(POIDS_6M),
        "alerte_concentration": alerte_concentration,
        "alerte_vide": alerte_vide,
        "html_top": html_top,
        "html_rangs": html_rangs,
        "html_exclues": html_exclues,
    }
    # Substitution simple plutot que %-formatting : le CSS contient des '%'
    # (width:100%, etc.) qui casseraient le formatage Python.
    sortie = TEMPLATE
    for cle, valeur in valeurs.items():
        sortie = sortie.replace("{{" + cle + "}}", valeur)
    return sortie


TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Momentum PEA</title>
<style>
  :root{
    --bg:#0f1110; --panel:#171a18; --panel2:#1e221f; --bord:#2c322e;
    --texte:#e9e7e0; --gris:#8d948c; --or:#c9a24b; --or2:#7a6431;
    --pos:#5aab7c; --neg:#c9614f;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --serif:"Iowan Old Style","Palatino Linotype",Georgia,serif;
    --sans:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--texte);font-family:var(--sans);
       padding:22px 14px 60px;line-height:1.5}
  .wrap{max-width:900px;margin:0 auto}
  h1{font-family:var(--serif);font-weight:400;font-size:1.8rem;margin:0 0 4px}
  .sous{color:var(--gris);font-size:.85rem;margin-bottom:18px}
  .demo{background:#4a1d14;color:#ffb4a2;border:1px solid var(--neg);
        padding:10px 14px;border-radius:4px;margin-bottom:16px;font-weight:600;
        font-size:.85rem;text-align:center;letter-spacing:.5px}
  .panneau{background:var(--panel);border:1px solid var(--bord);border-radius:5px;
           padding:16px;margin-bottom:16px}
  .panneau h2{font-family:var(--serif);font-weight:400;font-size:1.1rem;margin:0 0 12px}
  .cal{display:flex;gap:20px;flex-wrap:wrap;font-family:var(--mono);font-size:.85rem}
  .cal div span{color:var(--gris);font-size:.72rem;display:block;font-family:var(--sans)}
  .carte-top{display:flex;align-items:center;gap:12px;padding:12px;
             background:var(--panel2);border:1px solid var(--or2);
             border-radius:4px;margin-bottom:8px}
  .rang{font-family:var(--mono);font-size:1.5rem;color:var(--or);width:26px;text-align:center}
  .carte-corps{flex:1;min-width:0}
  .carte-nom{font-weight:600;font-size:.95rem}
  .carte-meta{color:var(--gris);font-size:.74rem}
  .carte-score{font-family:var(--mono);font-size:1.1rem}
  table{width:100%;border-collapse:collapse;font-size:.82rem}
  th{text-align:left;color:var(--gris);font-weight:400;font-size:.7rem;
     padding:7px 6px;border-bottom:1px solid var(--bord);text-transform:uppercase;
     letter-spacing:.4px}
  td{padding:8px 6px;border-bottom:1px solid var(--bord);vertical-align:top}
  td.num{text-align:right;font-family:var(--mono);white-space:nowrap}
  tr.retenue{background:rgba(201,162,75,.07)}
  tr.exclue{opacity:.42}
  .score{font-weight:600}
  .pos{color:var(--pos)} .neg{color:var(--neg)}
  .petit{color:var(--gris);font-size:.72rem}
  .alerte{background:#33270f;color:#d9a655;border:1px solid var(--or2);
          padding:10px 14px;border-radius:4px;margin-bottom:12px;font-size:.82rem}
  .note{color:var(--gris);font-size:.78rem;line-height:1.6}
  .tblwrap{overflow-x:auto}
  footer{color:var(--gris);font-size:.72rem;text-align:center;margin-top:24px}
</style>
</head>
<body>
<div class="wrap">
  {{banniere_demo}}

  <h1>Momentum PEA</h1>
  <div class="sous">Classement mis a jour le {{horodatage}} · score = {{poids_3m}}% perf 3 mois + {{poids_6m}}% perf 6 mois · filtre de tendance SMA200</div>

  <div class="panneau">
    <h2>Prochaine fenetre de rotation</h2>
    <div class="cal">
      <div><span>Vente (dernier jour de bourse du mois)</span>{{date_vente}}</div>
      <div><span>Achat (premier jour de bourse du mois suivant)</span>{{date_achat}}</div>
    </div>
  </div>

  <div class="panneau">
    <h2>Lignes retenues (top {{top_n}})</h2>
    {{alerte_concentration}}
    {{alerte_vide}}
    {{html_top}}
  </div>

  <div class="panneau">
    <h2>Classement complet</h2>
    <div class="tblwrap">
    <table>
      <thead>
        <tr><th>#</th><th>ETF</th><th>3 mois</th><th>6 mois</th><th>Score</th><th>vs SMA200</th></tr>
      </thead>
      <tbody>
        {{html_rangs}}
        {{html_exclues}}
      </tbody>
    </table>
    </div>
  </div>

  <div class="panneau">
    <h2>A savoir</h2>
    <p class="note">
      Les performances sont calculees sur les cours de cloture ajustes des dividendes,
      ce qui permet de comparer sans biais un ETF distribuant et un ETF capitalisant.
      Les lignes sous leur SMA200 sont exclues du classement, pas seulement mal classees :
      le filtre de tendance sert justement a ne pas acheter un actif qui rebondit
      dans une tendance de fond negative.
    </p>
    <p class="note">
      Le calendrier raisonne en "dernier jour de bourse du mois" et non en date fixe :
      fevrier, avril, juin, septembre et novembre n'ont pas de 31, et un 1er tombant
      un week-end n'est pas un jour de bourse.
    </p>
    <p class="note">
      Les performances passees ne prejugent pas des performances futures. Cet outil
      classe des chiffres, il ne predit rien.
    </p>
  </div>

  <footer>Source des cours : Yahoo Finance · page regeneree automatiquement</footer>
</div>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------

def construire_donnees_demo(etfs):
    """Chiffres fictifs, uniquement pour juger la mise en page hors ligne."""
    import random
    random.seed(7)
    lignes = []
    for e in etfs:
        p3 = random.uniform(-9, 14)
        p6 = random.uniform(-12, 22)
        ligne = dict(e)
        ligne.update({
            "perf_3M": p3,
            "perf_6M": p6,
            "ecart_sma200": random.uniform(-8, 11),
        })
        ligne["au_dessus_sma200"] = ligne["ecart_sma200"] > 0
        ligne["score"] = (p3 * POIDS_3M + p6 * POIDS_6M) / (POIDS_3M + POIDS_6M)
        lignes.append(ligne)
    return lignes


def main():
    demo = "--demo" in sys.argv

    config = json.loads(FICHIER_ETFS.read_text(encoding="utf-8"))
    etfs = [e for e in config["etfs"] if e.get("actif", True)]

    if demo:
        lignes = construire_donnees_demo(etfs)
    else:
        cache = charger_cache_symboles()
        lignes = []
        for e in etfs:
            ligne = dict(e)
            try:
                symbole = resoudre_symbole(e["isin"], cache)
                if not symbole:
                    raise RuntimeError("symbole introuvable pour l'ISIN")
                ligne["symbole"] = symbole
                historique = recuperer_historique(symbole)
                indicateurs = calculer_indicateurs(historique)
                if indicateurs is None:
                    raise RuntimeError("historique trop court")
                ligne.update(indicateurs)
                ligne["score"] = calculer_score(indicateurs)
                print("OK   %-42s %s" % (e["nom"][:42], symbole))
            except Exception as exc:
                # Une ligne en echec ne doit pas faire echouer tout le site :
                # elle apparait comme exclue, avec le motif affiche.
                ligne["erreur"] = str(exc)[:80]
                ligne["score"] = None
                ligne["au_dessus_sma200"] = None
                print("ECHEC %-42s %s" % (e["nom"][:42], exc))
            lignes.append(ligne)
            time.sleep(0.6)  # on reste courtois avec l'API
        sauver_cache_symboles(cache)

    horodatage = datetime.now().strftime("%d/%m/%Y a %H:%M")
    html = generer_html(lignes, horodatage, demo=demo)

    DOSSIER_SORTIE.mkdir(exist_ok=True)
    (DOSSIER_SORTIE / "index.html").write_text(html, encoding="utf-8")

    # On archive aussi les donnees brutes : utile pour verifier un calcul
    # ou reconstituer l'historique de tes decisions plus tard.
    (DOSSIER_SORTIE / "donnees.json").write_text(
        json.dumps(
            {"genere_le": datetime.now().isoformat(), "demo": demo, "etfs": lignes},
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )

    print("\nGenere : %s" % (DOSSIER_SORTIE / "index.html"))


if __name__ == "__main__":
    main()
