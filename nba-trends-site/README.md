# NBA Player Trends

A static website that shows every NBA player's season, last-10 and last-5 averages,
with hot/cold trend flags. It updates itself once a day for free.

## How it works

- `data/gamelog.js` holds one row per player per game. It is the only data file.
- `index.html` reads it and calculates every average and trend in the browser.
- `update_data.py` pulls newly finished games from ESPN's public JSON and appends them.
- `.github/workflows/daily.yml` runs the script every morning and commits the new data.
  GitHub Pages then republishes the site automatically.

## Put it online (about 5 minutes, free)

1. Create a new GitHub repository and upload everything in this folder (keep the `.github` folder).
2. Repository **Settings > Pages**: Source = "Deploy from a branch", Branch = `main`, folder `/ (root)`.
3. Repository **Actions** tab: open "Daily NBA stats update" and click **Run workflow** once to test it.
4. Your site is at `https://<your-username>.github.io/<repo-name>/`.

After that it refreshes every morning with no action from you.

## Preview locally

Open `index.html` in a browser. No server is needed.

## Things to know

- The data starts with opening night (Oct 20, 2026): 2 games, 35 players. Trends mean little
  until players have about 6 games. Flags show "n/a" until 3 games.
- ESPN's endpoints are unofficial and could change. If the daily run fails, the Actions tab
  shows the error, and the site simply keeps its last good data. Re-running backfills the last 3 days.
- `update_data.py` was tested offline against a mock box score, not yet against live finished games.
  Run the workflow manually the morning after a game night and check the log.
- Player names come from the source. If a name is spelled differently from the seed data,
  that player would appear as two rows. Fix the spelling in `data/gamelog.js`.
- Minutes are not tracked.
