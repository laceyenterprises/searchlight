"""The page shell every page shares: head, consent and tag markup, header and menu, footer."""
from __future__ import annotations

from .data import ANALYTICS_NOTICE, REPO_URL, esc
from .design import CSS, JS, SPRITE, g, mark

GTM_CONTAINER_ID = "GTM-TWQZV6F7"


GTM_HEAD = f"""<!-- Google consent defaults: analytics storage denied in the EEA, UK, Switzerland and listed territories; no consent UI; ad storage off everywhere -->
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}
gtag('consent','default',{{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'granted'}});
gtag('consent','default',{{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'denied',region:['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH','AX','GF','GP','MQ','RE','YT','MF','GI','JE','GG','IM']}});</script>
<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{GTM_CONTAINER_ID}');</script>
<!-- End Google Tag Manager -->"""
# No non-JavaScript iframe: it cannot apply the storage defaults above.


GTM_BODY = ""


SITE_URL = 'https://searchlightai.dev'


LACEY_URL = 'https://www.laceyenterprises.com'


CARD_SIZE = (1200, 630)


CARD_SCALE = 2


CARD_ALT_DEFAULT = 'Searchlight: an open benchmark of what web search does for AI coding agents.'


def _social_meta(title: str, description: str, active: str, alt: str) -> str:
    url = f'{SITE_URL}/' if active == 'index.html' else f'{SITE_URL}/{active}'
    image = f'{SITE_URL}/social-card.png'
    width, height = (n * CARD_SCALE for n in CARD_SIZE)
    tags = [('property', 'og:type', 'website'), ('property', 'og:site_name', 'Searchlight'),
            ('property', 'og:title', title), ('property', 'og:description', description),
            ('property', 'og:url', url), ('property', 'og:image', image),
            ('property', 'og:image:type', 'image/png'), ('property', 'og:image:width', str(width)),
            ('property', 'og:image:height', str(height)), ('property', 'og:image:alt', alt),
            ('name', 'twitter:card', 'summary_large_image'), ('name', 'twitter:title', title),
            ('name', 'twitter:description', description), ('name', 'twitter:image', image),
            ('name', 'twitter:image:alt', alt)]
    return (f'<link rel="canonical" href="{esc(url)}">'
            + ''.join(f'<meta {kind}="{key}" content="{esc(value)}">' for kind, key, value in tags))


NAV = [('index.html', 'Overview', 'beam'), ('results.html', 'Results', 'ranking'), ('studies.html', 'Studies', 'study'),
       ('learn.html', 'Learn', 'learn'), ('methodology.html', 'Methodology', 'method')]


NARROW = ('A narrow measurement: a fixed set of jobs on the agents and settings named, not a ranking of any provider '
          'or product as a whole.')


def _nav(active: str, pages: list[tuple[str, str, str]] = ()) -> str:
    def current(href):
        return ' aria-current="page"' if href == active else ''
    # A report or study page sits under Results or Studies; the bar marks that section.
    section = 'results.html' if active.startswith('report-') else 'studies.html' if active.startswith('study-') else active

    def in_section(href):
        return current(href) or (' aria-current="true"' if href == section else '')
    bar = ''.join(f'<a href="{href}"{in_section(href)}>{label}</a>' for href, label, _ in NAV)
    panel = ''.join(f'<a href="{href}"{current(href)}>{g(glyph)}{label}</a>' for href, label, glyph in NAV)
    if pages:
        panel += '<p class="sub">Reports and studies</p>' + ''.join(
            f'<a class="small" href="{href}"{current(href)}>{g(glyph)}{esc(title)}</a>' for href, title, glyph in pages)
    panel += f'<a class="small" href="{REPO_URL}">{g("open-source")}Source on GitHub</a>'
    return (f'<header class="top"><div class="wrap"><a class="brand" href="index.html" aria-label="Searchlight home">'
            f'{mark()}<span class="word">Searchlight</span></a>'
            f'<nav class="navbar" aria-label="Site">{bar}<a class="gh" href="{REPO_URL}">GitHub</a></nav>'
            f'<details class="menu"><summary aria-label="Menu">{g("menu-open", "open")}{g("menu-close", "close")}</summary>'
            f'<nav class="navpanel" aria-label="Site menu">{panel}</nav></details></div></header>')


def _footer() -> str:
    links = [(href, label) for href, label, _ in NAV] + [
        (REPO_URL, 'GitHub'), (f'{REPO_URL}/tree/main/reports', 'Raw data'),
        (f'{REPO_URL}/blob/main/LICENSE', 'Apache 2.0'), (f'{REPO_URL}/issues/new', 'Report a problem')]
    return (f'<footer class="foot"><div class="wrap"><div class="foot-top"><a class="brand" href="index.html">{mark()}'
            '<span class="word">Searchlight</span></a><nav class="foot-links" aria-label="Footer">'
            + ''.join(f'<a href="{href}">{label}</a>' for href, label in links) + '</nav></div>'
            f'<div class="foot-base"><p>A <a href="{LACEY_URL}">Lacey Enterprises</a> project · © 2026 Lacey Enterprises LLC</p>'
            f'<p class="analytics-disclosure">{ANALYTICS_NOTICE}</p></div></div></footer>')


def _document(title: str, description: str, active: str, main: str, script: str = '',
              social_title: str = '', card_alt: str = '', pages: list = (), head: str = '') -> str:
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" '
             'href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" '
             'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&'
             'family=JetBrains+Mono:wght@500;600&display=swap">')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'\n{GTM_HEAD}\n'
            f'<meta name="description" content="{esc(description)}">'
            f'{_social_meta(social_title or title, description, active, card_alt or CARD_ALT_DEFAULT)}'
            '<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)">'
            '<meta name="theme-color" content="#0C0F13" media="(prefers-color-scheme: dark)">'
            '<link rel="icon" type="image/svg+xml" href="logo.svg">'
            f'\n<title>{esc(title)}</title>\n{fonts}\n<style>{CSS}{head}</style>\n</head><body>\n'
            '<script>document.documentElement.classList.add("js")</script>\n'
            f'{GTM_BODY}\n{SPRITE}<a class="skip" href="#main">Skip to content</a>\n'
            f'{_nav(active, pages)}\n<main id="main">\n{main}\n</main>\n{_footer()}\n'
            f'<script>{JS}</script>\n{script}</body></html>\n')


def read_panel() -> str:
    """How to read these results, in full. It lives on the Learn page; every other page links to it."""
    return (f'<aside class="readme" id="read" aria-label="How to read these results">{g("method", "s32")}<div>'
            '<h3>How to read these results</h3>'
            f'<p>{NARROW} Different jobs, agents or settings can give a different order.</p>'
            '<ul><li>Every pass rate comes with its 95% range. When two ranges overlap, the test cannot separate '
            'them.</li>'
            '<li>Each benchmark is a few dozen runs per setup on one or two agents, at default settings.</li>'
            '<li>Studies ran each question once, so they report counts; a difference of a few items may be chance.</li>'
            '<li>A note beside a number says what qualifies it. Tap it to read.</li></ul>'
            '<p class="links"><a class="more" href="methodology.html">Read the method</a></p></div></aside>')


def read_link() -> str:
    return '<p class="readlink"><a class="more" href="learn.html#read">How to read these numbers</a></p>'
