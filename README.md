# Radary pro Drive HUD

Pevné radary a úseková měření v Česku, na Slovensku, v Polsku a v Rakousku z OpenStreetMap pro aplikaci
[Drive HUD](https://github.com/Svoby93CZ/drive_hud). Aplikace má databázi přibalenou a jednou týdně si odsud stáhne
novější: verze 1.7 a novější `cameras2.csv`, starší `cameras.csv`.

- Každé pondělí ji obnoví GitHub Actions (`.github/workflows/radary.yml`): stáhne výřezy OpenStreetMap jednotlivých
  zemí od [Geofabriku](https://download.geofabrik.de/europe.html), nástrojem osmium z nich vybere radary a silnice
  kolem úseků a radarů a skript `cameras.py` z nich udělá `cameras.csv` a `cameras2.csv`. Nové soubory se uloží, jen
  když se radary změnily. Ručně jde spustit v záložce Actions → Radary → Run workflow.
- Radary jsou body `highway=speed_camera` a zařízení relací `enforcement=maxspeed`, úseková měření relace
  `enforcement=average_speed` (od člena `from` k členu `to`).
- `cameras2.csv` ví navíc:
  - kterým směrem radar měří: u relace `enforcement=maxspeed` od členu `from` k členu `to`, u radaru stojícího na
    silnici z `direction=forward` nebo `backward` podle směru silnice. Hodnota ve stupních říká, kam míří objektiv,
    a mapuje se oběma způsoby, takže se nepoužije. Radar se směrem jen tam a radar bez něj se hlásí v obou směrech;
  - kudy úsek vede: trasa po silnicích od začátku ke konci (nejkratší, bez jízdy proti jednosměrce, po cestách
    vyznačených v relaci jako `section`, pokud tam jsou), zjednodušená na 5 m;
  - limit úseku: `maxspeed` relace, jinak limit silnice na většině trasy.
- Formát `cameras2.csv`: první řádek `# datum zdroje`, pak `c,šířka,délka,směr` pro radar (směr jízdy ve stupních,
  prázdný, když není známý), `s,limit,šířka,délka,šířka,délka,…` pro úsek s trasou a `e,limit,šířka,délka,šířka,délka`
  pro úsek, ke kterému se trasa nenašla (jen začátek a konec). Limit 0 = neznámý.
- Formát `cameras.csv`: první řádek `# datum zdroje`, pak `c,šířka,délka` pro radar a `s,šířka,délka,šířka,délka` pro úsek.
- Chybí-li radar, stačí ho doplnit do OpenStreetMap (openstreetmap.org nebo aplikace StreetComplete); do týdne
  se objeví i tady.
- GitHub vypíná plánované úlohy ve veřejném repozitáři, ve kterém se 60 dní nic nezměnilo. Pokud k tomu dojde,
  stačí v záložce Actions úlohu znovu zapnout.

Data © přispěvatelé OpenStreetMap, licence [ODbL 1.0](https://www.openstreetmap.org/copyright).
