"""Deterministic offline drafts. They explain a way to think and do not invent citations."""

import hashlib

from app.core.errors import AppError
from app.schemas.content import FactualClaim, ScriptContent
from app.services.scenes import scenes_from_narration
from app.services.textutil import estimate_seconds, split_sentences

_EN_LONG = (
    "Start by defining the words. When a video talks about a model, it means a program that turns examples into a pattern it can apply to a new case. That is the whole idea. Everything else is a detail about the examples, the check, and the limit.",
    "The pattern is not a memory of every example. The program keeps a compressed habit of guessing. After training, you can show it an input it did not store word for word, and it still produces an output. That output is a guess shaped by the earlier examples. Treat it as a guess until you have a reason to treat it as more.",
    "Examples are the quiet part of the story. They decide which situations the pattern has any right to cover. If the examples all come from one place, one period, or one kind of person, the pattern is about that slice. It can still be useful inside the slice. It is not a description of the world outside it. Say the slice out loud when you explain the result.",
    "There is a difference between a measurement and a story about the measurement. A measurement tells you what was recorded under a stated method. A story adds a cause, a recommendation, or a future. Those additions can be reasonable, and they can also outrun the record. Keep them in separate sentences so a viewer can tell which is which.",
    "A good check is something a second person could repeat. Name the input, the output, and the rule you will use to call the output wrong. If the only check is that the output sounds fluent, you are checking style, not truth. Fluency is cheap. A traceable check is the part worth keeping.",
    "Limits belong in the narration, not in a footnote you hope nobody reads. Say what the method cannot see. Say which decision you refuse to hand to the program. A limit is not a failure of the video. It is how the video stays honest when the topic is larger than the evidence in front of you.",
    "Questions are a better engine than claims you cannot point to. Ask what the system saw, what it never saw, and who is accountable for the next action. Those questions work for a lab result, a product demo, and a headline about the future. They do not require a number you invented to sound precise.",
    "Future talk needs a tense that matches your knowledge. You can say what a design is trying to do. You can say what would have to be true for a hope to land. You cannot know a date you did not measure. When the video is about the future, keep the sentence in the language of a possibility, and point to the present fact it hangs on.",
    "Compare sources before you compare opinions. If two reports disagree, look for the sentence each one actually wrote. Maybe they measured different things. Maybe one is a press summary of the other. Maybe neither states the claim that the headline suggests. The careful move is to quote the scope, not to average the vibes.",
    "Close by handing the viewer a way to continue without you. Tell them what document to open next, or what question to ask the next person who sounds certain. A short lesson is finished when the viewer can repeat the check. It is not finished when they can only repeat your conclusion.",
    "None of this requires a secret technique. It is a habit of separating the record, the guess, and the decision. Use that habit on tools, on science news, and on any interesting claim that arrives without a link. The link is the work. The narration is just how you show the work without inventing a fact.",
    "When you show a comparison, say what is being held constant. A bigger number is not a better result if the method changed underneath it. The viewer needs a baseline: the older method, a human check, or the empty case where the program did nothing. Without a baseline, a change has no size. You can still describe the direction. Refuse a precise claim of improvement that you cannot point to in a source.",
    "Tools that write text add a special trap. They can produce a paragraph that sounds like a citation without opening the document. Your script should not copy that habit. If a name, a date, a percentage, or a quotation appears, it needs a source you have read. If you do not have the source, cut the sentence. A gap is more trustworthy than a smooth invention.",
    "Keep the ending practical. Give one action that takes a few minutes: open the paper, find the methods section, or write down the check you will use next time. Do not ask the viewer to believe a field has been settled. Ask them to borrow a habit. Match every spoken claim to a sentence someone else published, or mark it unverified and leave it out.",
    "Repeat the frame when you are unsure. Record, guess, decision. If a sentence does not fit one of those three, it does not belong in the narration yet. That pause is the whole method, and it scales from a short vertical video to a longer cut.",
)

_CS_LONG = (
    "Nejdřív si vymezme slova. Když video mluví o modelu, jde o program, který z příkladů složí vzor a ten pak zkusí na novém případu. To je celý základ. Zbytek je detail o příkladech, o kontrole a o hranici, za kterou už nemluvíte z pramene.",
    "Vzor není skříň, ve které leží každý příklad. Program si nechává zhuštěný návyk, jak hádat. Po učení mu ukážete vstup, který nemá schovaný slovo od slova, a on přesto něco vydá. Ten výstup je odhad vytvarovaný staršími příklady. Nechte ho odhadem, dokud nemáte důvod brát ho vážněji.",
    "Příklady jsou tichá část vyprávění. Určují, na jaké situace smí vzor vůbec sahat. Když všechny pocházejí z jednoho místa, z jednoho období nebo od jedné skupiny lidí, vzor popisuje tenhle výsek. Uvnitř výseku může pomoct. Mimo něj to není obraz světa. Když výsledek říkáte, řekněte i ten výsek.",
    "Měření a vyprávění o měření se nesmí slepit do jedné věty. Měření říká, co se při daném postupu zaznamenalo. Příběh k tomu přidá příčinu, radu nebo budoucnost. Přídavek může být rozumný a taky může záznamu utéct. Držte je odděleně, ať je slyšet, co je co.",
    "Dobrá kontrola je taková, kterou zopakuje druhý člověk. Pojmenujte vstup, výstup a pravidlo, podle kterého výstup označíte za chybný. Když kontrolujete jen to, že text plyne, kontrolujete styl. Plynulost je levná. Drahá, a proto užitečná, je kontrola, která se dá dohledat.",
    "Limity patří do řeči, ne do poznámky pod čarou. Řekněte, co metoda nevidí. Řekněte, které rozhodnutí programu nedáte. Limit není ostuda videa. Díky němu video zůstane poctivé ve chvíli, kdy je téma větší než důkazy na stole.",
    "Otázka unese víc než tvrzení, na které neumíte ukázat. Ptejte se, co systém viděl, co neviděl a kdo nese další krok. Ta trojice sedí na laboratorní výsledek, na ukázku nástroje i na titulek o tom, co teprve přijde. Nepotřebuje číslo, které jste doplnili jen proto, aby věta zněla přesně.",
    "Řeč o budoucnosti musí mít čas, který odpovídá vašemu vědění. Můžete říct, o co se návrh snaží. Můžete říct, co by muselo platit, aby naděje dopadla. Nemůžete znát datum, které nikdo nezměřil. Nechte takovou větu v možnosti a ukažte na přítomný fakt, o který se opírá.",
    "Než srovnáte názory, srovnejte prameny. Když se dvě zprávy rozcházejí, hledejte větu, kterou každá opravdu napsala. Třeba měřily něco jiného. Třeba je jedna jen převyprávěním druhé. Třeba ani jedna neříká to, co slibuje titulek. Pečlivý krok je ocitovat rozsah, ne sladit dojem.",
    "Na konci nechte divákovi cestu, která vás nepotřebuje. Řekněte, který dokument otevřít, nebo jakou otázku položit dalšímu jistému hlasu. Lekce je hotová, když divák umí zopakovat kontrolu. Nestačí, že umí zopakovat vaši pointu.",
    "Není v tom žádný skrytý trik. Je to návyk oddělit záznam, odhad a rozhodnutí. Hodí se na nástroje, na vědecké zprávy i na zajímavé tvrzení, které přijde bez odkazu. Odkaz je práce. Řeč je jen způsob, jak tu práci ukázat a nic si nedomýšlet.",
    "U srovnání řekněte, co zůstalo stejné. Větší číslo není lepší výsledek, když se pod ním změnil postup. Divák potřebuje základ: starší metodu, lidskou kontrolu, nebo případ, kdy program nedělal nic. Bez základu nemá změna velikost. Směr popsat můžete. Přesné zlepšení, na které neukážete v prameni, do řeči nepatří.",
    "Nástroje, které píšou text, mají zvláštní past. Svedou odstavec, který zní jako citace, a přitom žádný dokument neotevřely. Scénář ten zvyk nemá kopírovat. Jméno, datum, procento nebo citát potřebují pramen, který jste četli. Když ho nemáte, větu vyškrtněte. Díra je důvěryhodnější než hladký výmysl.",
    "Konec nechte praktický. Dejte jeden krok na pár minut: otevřít studii, najít pasáž o metodě, nebo si zapsat kontrolu na příště. Nežádejte víru, že je obor uzavřený. Nabídněte návyk. Každé mluvené tvrzení buď sedí na cizí publikované větě, nebo je neověřené a do videa nejde.",
    "Když váháte, zopakujte rám. Záznam, odhad, rozhodnutí. Věta, která nesedí ani do jednoho, do vyprávění ještě nepatří. Ta pauza je celá metoda a unese ji krátké video i delší sestřih.",
    "Stejný postup platí, ať mluvíte o laboratoři, o nástroji, nebo o zprávě, která slibuje převrat. Nejdřív otázka. Pak to, co se opravdu zapsalo. Pak odhad, který tak pojmenujete. A nakonec rozhodnutí, které zůstává na člověku. Když jeden z těch kroků chybí, video ještě není hotové. Je to kostra. Doplní ji pramen, ne sebevědomé číslo, které v žádné tabulce není.",
    "Delší stopáž není dovolení si vymýšlet. Je to prostor pro limity, pro otázky a pro větu, kterou umíte ukázat v dokumentu. Když takovou větu nemáte, řekněte to rovnou a jděte dál.",
)


def _templates() -> list[dict]:
    shared_visual = (
        "Original type card on a dark teal field. Large short line, no stock photo, "
        "no logo, no fake screenshot."
    )
    return [
        {
            "template_key": "learn",
            "name": "Learn from examples",
            "language": "en",
            "format": "short",
            "categories": ["ai"],
            "hook": "Machines learn by example.",
            "narration": (
                "Machines learn by example. A model sees many inputs paired with outcomes, then "
                "adjusts until its guesses move closer. It does not understand a topic the way you "
                "do. It keeps patterns. The examples you give it decide what those patterns can "
                "cover. Narrow examples make a narrow result. Before you trust an answer, ask three "
                "plain questions. What did the system see? What was left out? Where should a person "
                "still decide? Use the output as a draft you can check. It is not a source of truth, "
                "and it is not a substitute for the source you would cite."
            ),
            "music_mood": "curious",
            "cta": "Check a source before you repeat a claim.",
            "description": "Offline draft about how a model learns. No fabricated citations.",
            "hashtags": ["#AI", "#Technology", "#HowItWorks", "#Science", "#StayCurious"],
            "thumbnail_text": "Patterns, not answers",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "learn",
            "name": "Učení z příkladů",
            "language": "cs",
            "format": "short",
            "categories": ["ai"],
            "hook": "Stroje se učí z příkladů.",
            "narration": (
                "Stroje se učí z příkladů. Model vidí spoustu vstupů spárovaných s výsledky a "
                "upravuje odhad, dokud se trefuje líp. Tématu nerozumí jako vy. Drží vzory. O tom, "
                "co ty vzory unesou, rozhodují příklady, které dostal. Úzká sada příkladů znamená "
                "úzký výsledek. Než odpovědi uvěříte, položte tři prosté otázky. Co systém viděl? "
                "Co v datech chybělo? Kde má pořád rozhodnout člověk? Berte výstup jako návrh, který "
                "jde ověřit. Není to zdroj pravdy a nenahrazuje pramen, který byste uvedli."
            ),
            "music_mood": "curious",
            "cta": "Než tvrzení zopakujete, najděte pramen.",
            "description": "Offline návrh o tom, jak se model učí. Bez vymyšlených citací.",
            "hashtags": ["#AI", "#Technologie", "#JakToFunguje", "#Veda", "#Overujte"],
            "thumbnail_text": "Vzory, ne odpovědi",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "tools",
            "name": "Tools move the work",
            "language": "en",
            "format": "short",
            "categories": ["technology"],
            "hook": "New tools move the work.",
            "narration": (
                "New tools move the work. They rarely erase it. A useful tool takes a step you "
                "already understand and makes that step faster to repeat. The risk starts when you "
                "hand over a step you cannot check. Begin with the job, not with the tool. Write "
                "down the input, the output, and the person who owns the result. Then choose which "
                "part a program may draft. Keep the review where a mistake would be expensive. "
                "Speed helps only after you know what correct means for that task. If you cannot "
                "describe the check, you are not ready to automate it."
            ),
            "music_mood": "focused",
            "cta": "Write the check before you automate the step.",
            "description": "Offline draft about tools and review. No fabricated citations.",
            "hashtags": ["#Technology", "#AI", "#Workflow", "#HowItWorks", "#BuildInPublic"],
            "thumbnail_text": "Check the step",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "tools",
            "name": "Nástroj a práce",
            "language": "cs",
            "format": "short",
            "categories": ["technology"],
            "hook": "Nástroj posouvá práci dál.",
            "narration": (
                "Nástroj posouvá práci dál. Málokdy ji celou zruší. Užitečný nástroj vezme krok, "
                "kterému rozumíte, a nechá vás ho opakovat rychleji. Riziko začíná tam, kde "
                "předáte krok, který neumíte zkontrolovat. Začněte úkolem, ne nástrojem. Sepište "
                "vstup, výstup a člověka, který za výsledek ručí. Teprve pak určete, co smí program "
                "navrhnout. Kontrolu si nechte u míst, kde je chyba drahá. Rychlost pomůže až ve "
                "chvíli, kdy víte, co je u daného úkolu správně. Když kontrolu neumíte popsat, "
                "ještě není čas ji předávat."
            ),
            "music_mood": "focused",
            "cta": "Nejdřív popište kontrolu, pak teprve automatizaci.",
            "description": "Offline návrh o nástrojích a kontrole. Bez vymyšlených citací.",
            "hashtags": ["#Technologie", "#AI", "#Prace", "#JakToFunguje", "#Overujte"],
            "thumbnail_text": "Kontrolujte krok",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "question",
            "name": "Start with the question",
            "language": "en",
            "format": "short",
            "categories": ["science", "future"],
            "hook": "Start with the question.",
            "narration": (
                "Start with the question. A result without a question is a number you cannot use. "
                "Say what would change your mind before you go looking for an answer. Separate what "
                "someone measured from what someone guessed. Name the limit of the method in a "
                "single sentence. If you cannot name that limit, you are not ready to explain the "
                "result. Curiosity is the opening. Care is the decision to stop at the edge of what "
                "you can support. That edge is the useful part of a science story. Stay inside it, "
                "and say when you do not know."
            ),
            "music_mood": "calm",
            "cta": "Name the limit before you explain the result.",
            "description": "Offline draft about questions and limits. No fabricated citations.",
            "hashtags": ["#Science", "#Future", "#CriticalThinking", "#Technology", "#AI"],
            "thumbnail_text": "Name the limit",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "question",
            "name": "Začněte otázkou",
            "language": "cs",
            "format": "short",
            "categories": ["science", "future"],
            "hook": "Začněte samotnou otázkou.",
            "narration": (
                "Začněte samotnou otázkou. Výsledek bez otázky je číslo, se kterým nic neuděláte. "
                "Ještě než začnete hledat odpověď, řekněte, co by vám změnilo názor. Oddělte to, "
                "co někdo změřil, od toho, co někdo jen odhadl. Limit metody pojmenujte jednou "
                "větou. Když to nedokážete, ještě nejste připravení výsledek vysvětlovat. "
                "Zvědavost je začátek. Pečlivost je rozhodnutí zastavit se na hraně toho, co "
                "unesete. Právě ta hrana je na vědeckém příběhu užitečná. Držte se v ní a řekněte, "
                "když nevíte."
            ),
            "music_mood": "calm",
            "cta": "Než výsledek vysvětlíte, pojmenujte limit.",
            "description": "Offline návrh o otázce a limitu metody. Bez vymyšlených citací.",
            "hashtags": ["#Veda", "#Budoucnost", "#Technologie", "#AI", "#Overujte"],
            "thumbnail_text": "Pojmenujte limit",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "fact",
            "name": "One claim, one source",
            "language": "en",
            "format": "short",
            "categories": ["facts"],
            "hook": "One claim, one source.",
            "narration": (
                "One claim, one source. A fact video earns trust when the sentence you say is a "
                "sentence you can point to. Pick a single claim. Find the document, paper, or "
                "dataset that states it. Read the lines around it, not only the headline. If the "
                "source does not say it, do not say it. If you are unsure, mark the line unverified "
                "and leave it out of the narration. Interesting is optional. Traceable is the rule. "
                "Your viewer should be able to find the same sentence you used. That is the whole craft."
            ),
            "music_mood": "focused",
            "cta": "If the source does not say it, leave it out.",
            "description": "Offline draft about sourcing a fact. It states no sourced fact itself.",
            "hashtags": ["#Facts", "#Science", "#MediaLiteracy", "#Sources", "#AI"],
            "thumbnail_text": "One claim only",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "fact",
            "name": "Jedno tvrzení",
            "language": "cs",
            "format": "short",
            "categories": ["facts"],
            "hook": "Jedno tvrzení, jeden pramen.",
            "narration": (
                "Jedno tvrzení, jeden pramen. Video o faktu si důvěru zaslouží, když věta, kterou "
                "řeknete, je věta, na kterou umíte ukázat. Vyberte jediné tvrzení. Najděte "
                "dokument, studii nebo datovou sadu, která ho říká. Přečtěte i řádky kolem, ne jen "
                "titulek. Když to pramen neříká, neříkejte to ani vy. Když si nejste jistí, "
                "označte řádek jako neověřený a do vyprávění ho nedávejte. Zajímavost je volitelná. "
                "Dohledatelnost je pravidlo. Divák by měl najít tutéž větu, ze které jste vyšli. "
                "V tom je celé řemeslo."
            ),
            "music_mood": "focused",
            "cta": "Když to pramen neříká, do videa to nedávejte.",
            "description": "Offline návrh o dohledání faktu. Sám žádný konkrétní fakt netvrdí.",
            "hashtags": ["#Fakta", "#Veda", "#Prameny", "#AI", "#Overujte"],
            "thumbnail_text": "Jedno tvrzení",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "careful",
            "name": "A careful longer cut",
            "language": "en",
            "format": "long",
            "categories": ["ai", "technology", "future", "science", "facts"],
            "hook": "Start by defining the words.",
            "narration": " ".join(_EN_LONG),
            "music_mood": "calm",
            "cta": "Match each spoken claim to a source, or leave it out.",
            "description": "Long offline draft about careful explanations. No fabricated citations.",
            "hashtags": ["#AI", "#Science", "#Technology", "#CriticalThinking", "#Sources"],
            "thumbnail_text": "Record, guess, decision",
            "visual_instructions": shared_visual,
        },
        {
            "template_key": "careful",
            "name": "Delší pečlivý sestřih",
            "language": "cs",
            "format": "long",
            "categories": ["ai", "technology", "future", "science", "facts"],
            "hook": "Nejdřív si vymezme slova.",
            "narration": " ".join(_CS_LONG),
            "music_mood": "calm",
            "cta": "Každé tvrzení opřete o pramen, nebo ho vynechte.",
            "description": "Dlouhý offline návrh o pečlivém vysvětlování. Bez vymyšlených citací.",
            "hashtags": ["#AI", "#Veda", "#Technologie", "#Prameny", "#Overujte"],
            "thumbnail_text": "Záznam, odhad, krok",
            "visual_instructions": shared_visual,
        },
    ]


TEMPLATES = _templates()


def iter_template_records() -> list[dict]:
    return TEMPLATES


def template_report() -> list[str]:
    lines = []
    for item in TEMPLATES:
        narr = estimate_seconds(item["narration"], item["language"])
        hook = estimate_seconds(item["hook"], item["language"])
        lines.append(
            f"{item['template_key']} {item['language']} {item['format']} "
            f"narr={narr}s hook={hook}s start={item['narration'].startswith(item['hook'])} "
            f"sentences={len(split_sentences(item['narration']))}"
        )
    return lines


def _select(category: str, title: str, language: str, video_format: str, template_key: str | None):
    if template_key:
        matches = [
            item
            for item in TEMPLATES
            if item["template_key"] == template_key
            and item["language"] == language
            and item["format"] == video_format
        ]
        if not matches:
            raise AppError(
                "No paired offline template exists for this language. "
                "Natural localization of a custom script needs OPENAI_API_KEY."
            )
        return matches[0]
    pool = [
        item
        for item in TEMPLATES
        if item["language"] == language
        and item["format"] == video_format
        and (video_format == "long" or category in item["categories"])
    ]
    if not pool:
        pool = [item for item in TEMPLATES if item["language"] == language and item["format"] == video_format]
    if not pool:
        raise AppError(f"No offline template for {language} {video_format}.")
    digest = hashlib.sha256(f"{category}|{title}|{language}|{video_format}".encode()).hexdigest()
    return pool[int(digest, 16) % len(pool)]


def _fits(narration: str, language: str, video_format: str) -> bool:
    low, high = (30, 60) if video_format == "short" else (5 * 60, 10 * 60)
    seconds = estimate_seconds(narration, language)
    return low <= seconds <= high


def _insert_topic(narration: str, hook: str, language: str, title: str) -> str:
    short = " ".join(title.split()[:12])
    if language == "cs":
        sentence = f"Dnes se držíme tématu {short}."
    else:
        sentence = f"The topic is {short}."
    rest = narration[len(hook) :].lstrip()
    return f"{hook} {sentence} {rest}"


def _claim(language: str) -> FactualClaim:
    if language == "cs":
        text = "Tenhle offline návrh netvrdí datovaný, číselný ani připsaný fakt."
    else:
        text = "This offline draft does not assert a dated, numeric, or attributed fact."
    return FactualClaim(text=text, sources=[], unverified=True, verified=False)


def build_offline_script(
    title: str,
    category: str,
    language: str,
    video_format: str,
    description: str = "",
    template_key: str | None = None,
) -> ScriptContent:
    template = _select(category, title, language, video_format, template_key)
    base = " ".join(split_sentences(template["narration"]))
    hook = template["hook"]
    with_topic = " ".join(split_sentences(_insert_topic(base, hook, language, title)))
    narration = (
        with_topic if with_topic.startswith(hook) and _fits(with_topic, language, video_format) else base
    )
    visual = template["visual_instructions"]
    topic_title = title.strip()[:140]
    if language == "cs":
        blurb = f"{template['description']} Téma: {topic_title}."
    else:
        blurb = f"{template['description']} Topic: {topic_title}."
    if description.strip():
        blurb = f"{blurb} {description.strip()}"
    return ScriptContent(
        title=topic_title,
        hook=hook,
        narration=narration,
        scenes=scenes_from_narration(narration, visual, video_format),
        visual_instructions=visual,
        music_mood=template["music_mood"],
        cta=template["cta"],
        description=blurb[:500],
        hashtags=list(template["hashtags"]),
        thumbnail_text=template["thumbnail_text"],
        factual_claims=[_claim(language)],
        offline_draft=True,
        format=video_format,
        language=language,
        template_key=template["template_key"],
        provider="offline",
    )
