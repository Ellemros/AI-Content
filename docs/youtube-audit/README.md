# Jak vyplnit YouTube API Audit and Quota Extension Form

Formulář: <https://support.google.com/youtube/contact/yt_api_form>

Do formuláře piš **anglicky**. Texty v rámečcích jsou připravené ke zkopírování. Hranaté závorky `[...]` nahraď svými údaji: jméno, adresa, e-mail. Tvé osobní údaje jsem nikam nevyplňoval.

Proč se audit podává: bez něj zůstávají videa nahraná přes API **soukromá**. Výchozí kvóta (10 000 jednotek denně, tedy zhruba 6 uploadů) nám stačí. Žádáme hlavně o povolení nahrávat veřejně, navýšení kvóty potřebujeme jen malé.

## Sekce 1: Request Type
- Zaškrtni **Complete a compliance audit to request for additional quota**. Je to jediná možnost pro první audit.

## Sekce 2: Organization and Contact Information
- **Are you applying:** As an individual user. (Pokud máš živnost nebo firmu, zvol „organization“ a vyplň její údaje.)
- **Your Full Legal Name:** `[tvé jméno a příjmení]`
- **Your Organization's Legal Name:** `self`
- **Parent Company Name:** `self`
- **Your Organization's Primary Website:** `https://github.com/Ellemros/AI-Content` (případně URL kanálu, až bude mít vlastní web)
- **Country:** Other → `Czech Republic`. Ulice, město, PSČ: tvoje kontaktní adresa. Do State/Province napiš kraj, například `Jihomoravský kraj`.
- **Category:** Creator Tools and Services
- **Organization Size / Type:** Independent Developer/Sole Proprietor
- **Primary Contact:** tvé jméno a e-mail, ideálně ten, pod kterým je Google Cloud projekt.
- **Technical Contact** a **Business Contact:** zaškrtni „Same as Primary Contact“.

## Sekce 3: Business Model
**Describe your organization's work as it relates to YouTube:**
```
I run Money Minds, an educational YouTube channel that explains the psychology of everyday money decisions (pricing, tipping, spending habits) using published, peer-reviewed research. Videos are original motion-graphics shorts: every claim is checked against the cited studies, the narration states the study's limitations, and nothing is financial advice.

The API client is a private, self-hosted tool that I alone use for my own channel. It produces a video, I personally watch and approve it, and only then does the tool upload that single video to my own channel with the YouTube Data API (videos.insert). It also reads my own channel's analytics (views, watch time, estimated revenue) so I can see which topics viewers find useful and compare production costs with revenue.

The tool has no other users, no public sign-up, and does not access any other channel or any viewer's personal data. Uploads are limited to about one video per day.
```
- **Target audience:** zaškrtni **Internal Users** a **Individual Content Creators**.
- **How does your API Client monetize:** zaškrtni **Other** a napiš: `The tool itself is not sold or offered to anyone. My channel may earn YouTube Partner Program ad revenue.`
- **Google Partner Manager:** No, I do not have a Google representative
- **How did you first learn about the API:** Google Developer Documentation
- **Content Owner ID** a **Google Ads Customer ID:** nech prázdné.

## Sekce 4: API Client Overview
- **API Client Name:** `Money Minds Content Tool` (Název nesmí obsahovat slovo „YouTube“. Totéž jméno dej i OAuth aplikaci v Google Cloud.)
- **Does the name contain "YouTube"?** No
- **Primary Access URL:** `https://github.com/Ellemros/AI-Content`
- **Privacy Policy URL:** `https://github.com/Ellemros/AI-Content/blob/main/PRIVACY.md` (Odkaz začne fungovat po sloučení PR do `main`. Do té doby použij `https://github.com/Ellemros/AI-Content/blob/phase-4/production-pilot/PRIVACY.md`.)
- **Terms of Service URL:** nech prázdné.
- **Is your API Client publicly accessible?** No (je to interní nástroj; kód je veřejný na GitHubu)

## Sekce 5: Use Cases and Quota
- **How many project numbers:** 1. Pak se objeví pole pro **Project number**. Najdeš ho v Google Cloud Console → Dashboard → Project info → *Project number*. Je to číslo, ne název projektu.
- Pokud formulář chce popis využití API nebo požadovanou kvótu, vlož:
```
API usage: videos.insert to upload my own approved videos to my own channel (about 1 per day, at most 3); channels.list and videos.list to confirm uploads; YouTube Analytics API (reports.query) for my own channel's daily views, watch time and estimated revenue.
Quota: about 1,600 units per upload plus a few hundred units of reads per day. The default 10,000 units per day is enough for now; I am mainly requesting the audit so that my uploads are not restricted to private. If extra quota is offered, 20,000 units per day would cover up to 10 uploads per day in the future.
```

## Sekce 6: Přílohy
- **Architecture Diagram:** nahraj `docs\youtube-audit\architecture-diagram.png`
- **User Flow Diagrams:** nahraj `docs\youtube-audit\user-flow-diagram.png`
- **Other Supporting Materials:** formulář u interních nástrojů chce kompletní skript pro upload, nebo záznam obrazovky.
  - Nahraj `scripts\youtube_upload.py`.
  - Kdyby formulář .py soubor nevzal, vlož do popisu v sekci 3 odkaz: `https://github.com/Ellemros/AI-Content/blob/phase-4/production-pilot/scripts/youtube_upload.py`.

## Sekce 7: Attestations
Zaškrtni všechna políčka, ale až si podmínky přečteš. Je to tvůj souhlas s podmínkami YouTube, za tebe ho dát nemůžu.

## Po odeslání
- Google se ozve e-mailem, obvykle za několik dní až týdnů. Může se doptávat, například poslat odkaz na kanál nebo ukázat upload na videu.
- **Kdyby audit neprošel:** systém dál vyrábí hotová MP4 i s titulkem a popisem a ty je nahraješ ručně přes YouTube Studio. Zabere to asi minutu na video. Dá se v něm nastavit i plánované zveřejnění na týden dopředu.
