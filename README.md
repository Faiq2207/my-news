# My News Channel: free automatic news website

What this website does on its own:

- **Live headlines** from 22 Pakistani and international channels, refreshed every 30 minutes. Every headline links to the original website.
- **20 of your own stories, twice a day** (8 AM and 8 PM Pakistan time): 10 Pakistan (8 news, 2 sports) and 10 World (8 news, 2 sports).
- **Facts only from sources.** Every story lists the reports it used. Any story with a number that isn't in its sources is thrown away automatically.
- **Auto-cleanup.** Everything older than 3 days is deleted.

Everything runs on free services: GitHub (hosting and scheduling) and Google AI Studio (writing).

---

## Setup (about 15 minutes, one time only)

### 1. Make a free GitHub account
Go to https://github.com/signup and sign up.

### 2. Upload the project
1. Click **+** (top right), then **New repository**.
2. Name it `my-news`, choose **Public**, then click **Create repository**.
3. Click **uploading an existing file**.
4. Unzip `news-site.zip`, open the folder, select **everything inside it**, and drag it onto the page.
   - On a Mac, press `Cmd + Shift + .` to show the hidden `.github` folder so it gets uploaded too.
5. Click **Commit changes**.

Check: your repository should show a `.github` folder. If it doesn't, click **Add file**, then **Create new file**, type `.github/workflows/headlines.yml` as the name, paste in that file's contents, and commit. Do the same for `articles.yml`.

### 3. Get a free AI key
1. Go to https://aistudio.google.com and sign in with a Google account.
2. Click **Get API key**, then **Create API key**, and copy it.

### 4. Give the key to your project
In your repository: **Settings**, then **Secrets and variables**, then **Actions**, then **New repository secret**.
- Name: `GEMINI_API_KEY`
- Secret: paste your key, then click **Add secret**.

### 5. Let the robot save data
**Settings**, then **Actions**, then **General**. Under **Workflow permissions**, choose **Read and write permissions** and click **Save**.

### 6. Turn on the website
**Settings**, then **Pages**. Under **Source**, choose **Deploy from a branch**, branch `main`, folder `/ (root)`, then click **Save**.

### 7. Run it the first time
1. Open the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Update headlines**, then **Run workflow**. Wait about a minute.
3. Click **Write articles**, then **Run workflow**. Wait about 2 minutes.

### 8. Open your site
`https://YOUR-GITHUB-NAME.github.io/my-news/`

From now on, it updates by itself.

---

## Changing things

| What | Where |
|---|---|
| Site name and tagline | `config.json` |
| Days to keep data | `keep_days` in `config.json` |
| Number of stories | `articles_news_per_section` and `articles_sports_per_section` in `config.json` |
| Channels and feeds | the `SOURCES` list in `news.py` |
| Story times | the `cron` line in `.github/workflows/articles.yml` (times are UTC; Pakistan is UTC+5) |

To edit a file on GitHub, open it, click the pencil icon, change it, and click **Commit changes**.

## If something doesn't work

- **No stories, but headlines work:** open **Actions**, then the last **Write articles** run, and read the log.
  - "GEMINI_API_KEY is missing": redo step 4. The name must be exactly `GEMINI_API_KEY`.
  - "AI error 404" or "model not found": Google renamed its model. In `news.py`, change `gemini-flash-latest` to the current free model name shown in AI Studio.
  - "AI service stayed busy": the free tier hit its limit. It will work at the next scheduled run.
- **A channel shows "via google-news" in the log:** its own feed failed, so the script used Google News results for that site instead. That's normal. Those links go through Google News to the original article.
- **The site shows nothing:** wait 2 minutes after the first run, then refresh. Also check step 6.
- **Updates stopped after about 2 months:** GitHub pauses schedules on quiet projects. Open **Actions** and re-enable the workflows.

## Good practice

- Our stories are AI-written from short headline snippets. Read them before sharing widely, especially war and political news.
- Keep the "AI assistance" note in the footer. Being open about it builds trust.
- Headlines and links belong to their publishers. The site shows only titles, short snippets and links, never full copied articles.
- Some news feeds allow only personal, non-commercial use. If you add ads later, check each source's terms or talk to a lawyer.
