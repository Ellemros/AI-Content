# Co musíš udělat ty (Money Minds)

Systém umí vyrobit video sám, od tématu po hotové MP4. Některé kroky ale za tebe udělat nemůže a nesmí: založit účty, souhlasit s podmínkami platforem, zaplatit a schválit první videa. Tady je celý seznam v pořadí, ve kterém je dělat. Příkazy jsou pro Windows CMD a spouštíš je ze složky projektu s aktivovaným `.venv`.

## 1. Hned teď (bez toho to nevydělá nic)

1. **Podívej se na videa a schval ta, která bys pustil ven.**
   - Hotová videa jsou v `output\review\money-minds-pilot\` a `output\review\money-minds-v2\`.
   - Schválení jednoho videa (ID úlohy najdeš v `summary.json` u každého videa jako `job_id`):
     ```
     python -m app.cli approve --job JOB_ID
     ```
   - Když v nějakém videu najdeš chybu:
     ```
     python -m app.cli flag-video --job JOB_ID --reason "co je špatně"
     ```
2. **Založ YouTube kanál Money Minds.**
   - Doporučuji „kanál značky“ (Brand Account) pod tvým Google účtem.
   - Vyplň popis, logo a banner.
   - V nastavení kanálu zvol „není určeno pro děti“.

## 2. Napojení YouTube (asi 30 minut, jednou)

1. Na <https://console.cloud.google.com> vytvoř projekt a zapni *YouTube Data API v3* a *YouTube Analytics API*.
2. Nastav *OAuth consent screen*:
   - typ External;
   - přidej se jako testovací uživatel.
3. Vytvoř *OAuth client* typu *Desktop app*. Stáhni jeho JSON a ulož ho jako `data\secrets\youtube_client_secret.json`. Do Gitu se nedostane.
4. Spusť příkaz níže a v prohlížeči potvrď přístup:
   ```
   python -m app.cli connect-youtube --brand money-minds
   ```
5. **Požádej o audit API** (formulář „YouTube API Services – Audit and Quota Extension“).
   - Dokud Google audit neschválí, zůstávají videa nahraná přes API **soukromá**.
   - Výchozí kvóta navíc stačí jen na zhruba 6 uploadů denně.
   - Audit trvá obvykle dny až týdny, proto o něj požádej co nejdřív.

## 3. Zapnutí publikování (až budeš mít schválená videa a napojený kanál)

Zapni publikování v režimu schvalování. Na YouTube pak půjdou jen videa, která jsi schválil, maximálně 1 denně.
```
python -m app.cli set-publishing --brand money-minds --mode approval --daily-limit 1
```
V souboru `.env` nastav `PUBLISHING_ENABLED=true`. Tuhle změnu dělám jen na tvůj pokyn.

**Doporučení:** 4–6 týdnů v režimu `approval`. Teprve potom přepni na `autonomous`. I v autonomním režimu systém nikdy nepublikuje video, které čeká na lidskou kontrolu.

## 4. Další platformy (volitelné, až poběží YouTube)

- **TikTok:**
  - vývojářská aplikace na developers.tiktok.com s *Content Posting API*;
  - bez auditu TikTok vidíš příspěvky jen ty;
  - program Creator Rewards platí jen za videa delší než 1 minutu a jen v některých zemích.
- **Instagram a Facebook:** Instagram Business účet propojený s Facebook stránkou a Meta aplikace se schválením `instagram_content_publish`.
- Podrobné kroky jsou v `README.md` v části *Publishing*.

## 5. Peníze a účty

| Co | Nutné? | Proč |
|---|---|---|
| **OpenAI kredit** | Ano | Píše scénáře (GPT-5.4). Pilot vyšel na **$0,13–0,50 za video**. Při 1 videu denně je to zhruba **$4–15 měsíčně**. Měsíční strop je v `.env` (`MONTHLY_SPEND_CAP_USD`). |
| **Gemini** | Už máš | Zdarma dělá řazení témat, výběr hooku, kontrolu faktů a editorskou kontrolu. Když dojde limit, systém sám přepne na OpenAI. **Pozor:** podle podmínek Googlu se bezplatná vrstva nesmí používat pro služby uživatelům v EU a vstupy z ní může Google použít ke zlepšení svých produktů. Nejbezpečnější je zapnout v Google Cloud **billing** (placenou vrstvu). U těchto kroků jde o centy měsíčně. Pak do `.env` doplň ceny `GEMINI_INPUT_COST_PER_MILLION` a `GEMINI_OUTPUT_COST_PER_MILLION`, aby se náklady počítaly. |
| **ElevenLabs** | Ne, zatím ne | Kokoro (zdarma) je srozumitelný, ale plošší. Až budeš mít první data o sledovanosti, můžeme hlasy porovnat. Bezplatný plán ElevenLabs komerční použití nedovoluje, takže by šlo o placený plán s komerční licencí. Hlas se nikdy neklonuje. |
| **AdSense a YouTube Partner Program** | Později | Přihlášení je možné až po splnění podmínek: 1 000 odběratelů a 10 milionů zhlédnutí Shorts za 90 dní, nebo 4 000 hodin sledování delších videí. |

## 6. Každý týden (asi 15 minut)

1. Projdi nová videa a schval je (`approve`) nebo vrať k opravě (`flag-video`).
2. Výrobu dalších videí spustíš jedním příkazem:
   ```
   python -m app.cli pilot --brand money-minds --videos 5 --budget 5 --out output\review\tyden-XX
   ```
   Když dojdou témata, systém si sám najde nová s ověřenými zdroji. Ručně to jde takto:
   ```
   python -m app.cli find-topics --brand money-minds --count 10
   ```
3. Až poběží publikování, stáhni statistiky a podívej se na výsledky:
   ```
   python -m app.cli fetch-analytics
   ```
   ```
   python -m app.cli profit-report --brand money-minds
   ```
