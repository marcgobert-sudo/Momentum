# Momentum PEA

Site statique qui classe des ETF éligibles au PEA selon leur momentum
(performance 3 mois et 6 mois), avec un filtre de tendance SMA200.
Il se régénère tout seul chaque jour de semaine après la clôture.

## Mise en route (environ 10 minutes)

1. Crée un dépôt GitHub (public ou privé, les deux marchent) et pousse-y
   ces fichiers.
2. Dans le dépôt : **Settings → Pages → Source → GitHub Actions**.
3. Onglet **Actions → Mise a jour momentum PEA → Run workflow** pour lancer
   la première génération à la main.
4. L'adresse du site s'affiche à la fin du job, du type
   `https://<ton-compte>.github.io/<nom-du-depot>/`.

Le site est ensuite régénéré automatiquement du lundi au vendredi à 18h30 UTC,
soit après la clôture d'Euronext Paris. Tu n'as plus rien à faire.

> Note : GitHub suspend les workflows planifiés d'un dépôt resté sans activité
> pendant 60 jours. Un simple commit ou un lancement manuel les réactive.

## Personnalisation

Tout se règle dans deux fichiers.

**`etfs.json`** — l'univers. Ajoute ou retire des lignes, et passe `actif` à
`false` pour exclure une ligne sans la supprimer. Le champ `bloc` sert à
l'alerte de concentration : deux ETF du même bloc (`US`, `Europe`, `Monde`…)
déclenchent un avertissement quand ils occupent les deux premières places, car
deux tickers différents sur la même zone ne diversifient pas.

Tu n'as pas besoin de connaître le ticker Yahoo : le script le résout depuis
l'ISIN et met le résultat en cache dans `symboles_cache.json`. C'est volontaire,
les tickers changent (la gamme Lyxor est passée chez Amundi, par exemple) alors
que l'ISIN, lui, est stable.

**`update.py`**, en haut du fichier :

```python
POIDS_3M = 50   # pondération de la performance 3 mois
POIDS_6M = 50   # pondération de la performance 6 mois
TOP_N    = 2    # nombre de lignes retenues
```

## Ce que fait le calcul

- Performances calculées sur les **cours de clôture ajustés des dividendes**,
  ce qui permet de comparer un ETF distribuant et un ETF capitalisant sans biais.
- **SMA200** = moyenne des 200 dernières clôtures. Une ligne sous sa SMA200 est
  *exclue* du classement, pas seulement mal classée.
- Une ligne dont les données sont indisponibles apparaît en exclue avec le motif,
  plutôt que de faire échouer la page entière.

## Calendrier

Le site affiche la prochaine fenêtre de rotation : dernier jour de bourse du mois
pour les ventes, premier jour de bourse du mois suivant pour les achats.

Il raisonne en « dernier jour de bourse » et non en date fixe, parce que février,
avril, juin, septembre et novembre n'ont pas de 31, et qu'un 1er tombant un
week-end n'est pas un jour de bourse.

## Test en local

```bash
python3 update.py          # récupère les vrais cours
python3 update.py --demo   # page de démonstration, données fictives
```

Aucune dépendance à installer, uniquement la bibliothèque standard Python.

## Limites à garder en tête

- Les cours viennent de Yahoo Finance, une source gratuite et non contractuelle :
  une valeur peut être manquante ou erronée un jour donné. Avant de passer un
  ordre, recoupe avec ton courtier.
- Les performances passées ne préjugent pas des performances futures. Cet outil
  classe des chiffres, il ne prédit rien.
