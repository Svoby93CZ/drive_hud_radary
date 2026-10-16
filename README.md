# Radary pro Drive HUD

Pevné radary a úseková měření v Česku, na Slovensku, v Polsku a v Rakousku z OpenStreetMap pro aplikaci
[Drive HUD](https://github.com/Svoby93CZ/drive_hud). Aplikace má databázi přibalenou a jednou týdně si odsud stáhne
novější `cameras.csv`.

- Každé pondělí ji obnoví GitHub Actions (`.github/workflows/radary.yml`): stáhne výřezy OpenStreetMap jednotlivých
  zemí od [Geofabriku](https://download.geofabrik.de/europe.html), nástrojem osmium z nich vybere radary a skript
  `cameras.py` z nich udělá `cameras.csv`. Nový soubor se uloží, jen když se radary změnily. Ručně jde spustit
  v záložce Actions → Radary → Run workflow.
- Radary jsou body `highway=speed_camera` a zařízení relací `enforcement=maxspeed`, úseková měření relace
  `enforcement=average_speed` (od člena `from` k členu `to`).
- Formát: první řádek `# datum zdroje`, pak `c,šířka,délka` pro radar a `s,šířka,délka,šířka,délka` pro úsek.
- Chybí-li radar, stačí ho doplnit do OpenStreetMap (openstreetmap.org nebo aplikace StreetComplete); do týdne
  se objeví i tady.
- GitHub vypíná plánované úlohy ve veřejném repozitáři, ve kterém se 60 dní nic nezměnilo. Pokud k tomu dojde,
  stačí v záložce Actions úlohu znovu zapnout.

Data © přispěvatelé OpenStreetMap, licence [ODbL 1.0](https://www.openstreetmap.org/copyright).
