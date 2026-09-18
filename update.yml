name: Mise a jour momentum PEA

on:
  schedule:
    # Tous les jours de semaine a 18h30 UTC (apres la cloture d'Euronext Paris,
    # qui ferme a 17h30 heure de Paris). Le cron GitHub est toujours en UTC.
    - cron: "30 18 * * 1-5"
  # Permet aussi de lancer la mise a jour a la main depuis l'onglet Actions,
  # utile le jour ou tu veux verifier avant de passer un ordre.
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Generer le site
        run: python3 update.py

      - name: Verifier que la page a bien ete produite
        run: test -s public/index.html

      - uses: actions/upload-pages-artifact@v3
        with:
          path: public

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
