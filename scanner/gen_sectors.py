#!/usr/bin/env python3
"""
Generate the seed lists for the three public-sector categories that sit alongside
the kommuner and the central-state organ (issue #26): fylkeskommuner,
helseforetak, and the UH-sektor (universiteter og høgskoler). Each becomes its
own committed seed JSON, scanned and shown as its own category.

This reuses gen_statlige_organ.py's pattern exactly — only the categories differ,
so they share one generator instead of three near-identical files. Each entity's
LEGAL IDENTITY (official name + website) is resolved live from the public
Brønnøysund Enhetsregisteret by its organisasjonsnummer (the citable source,
CLAUDE.md rule 1 / rule 4):

    https://data.brreg.no/enhetsregisteret/api/enheter/<orgnr>

The MAIL DOMAIN is NOT in Enhetsregisteret (it lists the website, which is not
always the mail domain — fylkeskommuner publish e.g. *fylke.no but the register
may carry no website at all). So the mail domain is curated below, each confirmed
to carry real public DNS mail signal; the scan then proves the platform from it.

Run:  python3 gen_sectors.py        # rewrites fylkeskommuner/helseforetak/uh_sektor.json
Needs network (Enhetsregisteret, no auth, no cost). The produced JSON is COMMITTED
so the scan stays offline; re-run this only to refresh names/websites.
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BRREG = "https://data.brreg.no/enhetsregisteret/api/enheter/{}"

# (display name, organisasjonsnummer, mail domain). Display name is editorial; the
# legal name + website come from the register; the mail domain is curated and
# confirmed against public DNS (dig MX/SPF/autodiscover).

# Fylkeskommuner — the 14 county authorities (post-2024 reorg; Oslo's fylke role
# sits in the kommune list). Identities from Enhetsregisteret.
FYLKE = [
    ("Akershus fylkeskommune",        "930580783", "akershusfylke.no"),
    ("Buskerud fylkeskommune",        "930580260", "buskerudfylke.no"),
    ("Østfold fylkeskommune",         "930580694", "ofk.no"),
    ("Innlandet fylkeskommune",       "920717152", "innlandetfylke.no"),
    ("Vestfold fylkeskommune",        "929882385", "vestfoldfylke.no"),
    ("Telemark fylkeskommune",        "929882989", "telemarkfylke.no"),
    ("Agder fylkeskommune",           "921707134", "agderfk.no"),
    ("Rogaland fylkeskommune",        "971045698", "rogfk.no"),
    ("Vestland fylkeskommune",        "821311632", "vestlandfylke.no"),
    ("Møre og Romsdal fylkeskommune", "944183779", "mrfylke.no"),
    ("Trøndelag fylkeskommune",       "817920632", "trondelagfylke.no"),
    ("Nordland fylkeskommune",        "964982953", "nfk.no"),
    ("Troms fylkeskommune",           "930068128", "tromsfylke.no"),
    ("Finnmark fylkeskommune",        "830090282", "ffk.no"),
]

# Helseforetak — the 4 regional health authorities (RHF) + the health trusts (HF).
# Many Helse Sør-Øst HFs route mail through the shared mx.sykehuspartner.no
# gateway; the deep probe unmasks the backend where it can (else honest Uavklart).
HELSE = [
    ("Helse Sør-Øst RHF",                   "991324968", "helse-sorost.no"),
    ("Helse Vest RHF",                      "983658725", "helse-vest.no"),
    ("Helse Midt-Norge RHF",                "983658776", "helse-midt.no"),
    ("Helse Nord RHF",                      "883658752", "helse-nord.no"),
    ("Oslo universitetssykehus HF",         "993467049", "oslo-universitetssykehus.no"),
    ("Akershus universitetssykehus HF",     "983971636", "ahus.no"),
    ("Sykehuset Østfold HF",                "983971768", "sykehuset-ostfold.no"),
    ("Vestre Viken HF",                     "894166762", "vestreviken.no"),
    ("Sykehuset i Vestfold HF",             "983975259", "siv.no"),
    ("Sykehuset Telemark HF",               "983975267", "sthf.no"),
    ("Sørlandet sykehus HF",                "983975240", "sshf.no"),
    ("Sykehuset Innlandet HF",              "983971709", "sykehuset-innlandet.no"),
    ("Helse Bergen HF",                     "983974724", "helse-bergen.no"),
    ("Helse Stavanger HF",                  "983974678", "sus.no"),
    ("Helse Fonna HF",                      "983974694", "helse-fonna.no"),
    ("Helse Førde HF",                      "983974732", "helse-forde.no"),
    ("St. Olavs hospital HF",               "883974832", "stolav.no"),
    ("Helse Møre og Romsdal HF",            "997005562", "helse-mr.no"),
    ("Helse Nord-Trøndelag HF",             "983974791", "hnt.no"),
    ("Nordlandssykehuset HF",               "983974910", "nordlandssykehuset.no"),
    ("Universitetssykehuset Nord-Norge HF", "983974899", "unn.no"),
    ("Helgelandssykehuset HF",              "983974929", "helgelandssykehuset.no"),
    ("Finnmarkssykehuset HF",               "983974880", "finnmarkssykehuset.no"),
    ("Sykehuspartner HF",                   "914637651", "sykehuspartner.no"),
    ("Sykehusapotekene HF",                 "992281618", "sykehusapotekene.no"),
]

# Universiteter og høgskoler (UH-sektoren) — the universities + state and
# accredited private høgskoler.
UH = [
    ("Universitetet i Oslo",                "971035854", "uio.no"),
    ("Universitetet i Bergen",              "874789542", "uib.no"),
    ("NTNU",                                "974767880", "ntnu.no"),
    ("UiT Norges arktiske universitet",     "970422528", "uit.no"),
    ("Universitetet i Stavanger",           "971564679", "uis.no"),
    ("Universitetet i Agder",               "970546200", "uia.no"),
    ("Universitetet i Sørøst-Norge",        "911770709", "usn.no"),
    ("Nord universitet",                    "970940243", "nord.no"),
    ("Universitetet i Innlandet",           "918108467", "inn.no"),
    ("OsloMet – storbyuniversitetet",       "997058925", "oslomet.no"),
    ("Norges miljø- og biovitenskapelige universitet (NMBU)", "969159570", "nmbu.no"),
    ("Norges handelshøyskole (NHH)",        "974789523", "nhh.no"),
    ("Høgskulen på Vestlandet",             "917641404", "hvl.no"),
    ("Høgskolen i Østfold",                 "971567376", "hiof.no"),
    ("Høgskolen i Molde",                   "971555483", "himolde.no"),
    ("Høgskulen i Volda",                   "974809672", "hivolda.no"),
    ("Arkitektur- og designhøgskolen i Oslo", "971526378", "aho.no"),
    ("Norges musikkhøgskole",               "974761106", "nmh.no"),
    ("Norges idrettshøgskole",              "971526033", "nih.no"),
    ("Kunsthøgskolen i Oslo",               "977027233", "khio.no"),
    ("Samisk høgskole",                     "971519363", "samas.no"),
    ("Politihøgskolen",                     "974761017", "phs.no"),
    ("VID vitenskapelige høgskole",         "915635520", "vid.no"),
    ("MF vitenskapelig høyskole",           "917387079", "mf.no"),
    ("Dronning Mauds Minne Høgskole",       "971574747", "dmmh.no"),
    ("NLA Høgskolen",                       "995189186", "nla.no"),
    ("Lovisenberg diakonale høgskole",      "994881078", "ldh.no"),
]

# key -> (curated list, output filename, dataset title fragment)
CATEGORIES = {
    "fylke": (FYLKE, "fylkeskommuner.json",
              "Norwegian county authorities (fylkeskommuner) — email-scan seed"),
    "helse": (HELSE, "helseforetak.json",
              "Norwegian health trusts (helseforetak: RHF + HF) — email-scan seed"),
    "uni":   (UH, "uh_sektor.json",
              "Norwegian higher education (universiteter og høgskoler) — email-scan seed"),
}


def fetch_entity(orgnr):
    """Official name + website + org-form for one organisasjonsnummer (cited source)."""
    with urllib.request.urlopen(BRREG.format(orgnr), timeout=20) as r:
        d = json.load(r)
    return {
        "legal_name": d["navn"],
        "website": d.get("hjemmeside") or None,
        "orgform": d["organisasjonsform"]["kode"],
    }


def build(curated, title, date):
    organ = []
    for name, orgnr, domain in curated:
        info = fetch_entity(orgnr)
        print(f"  {orgnr}  {info['legal_name']:48}  -> {domain}", file=sys.stderr)
        organ.append({
            "name": name,
            "legal_name": info["legal_name"],
            "orgnr": orgnr,
            "domain": domain,
            "website": info["website"],
            "orgform": info["orgform"],
            "source": BRREG.format(orgnr),
        })
    organ.sort(key=lambda o: o["name"])
    return {
        "meta": {
            "title": title,
            "source": "Brønnøysund Enhetsregisteret (legal name + website per orgnr)",
            "sourceUrl": "https://data.brreg.no/enhetsregisteret/api/enheter/",
            "sourceDate": date,
            "license": "CC BY 4.0",
            "selection": ("Identities resolved from Enhetsregisteret; mail domains "
                          "curated (the register lists the website, not the mail "
                          "domain) and confirmed against public DNS."),
            "count": len(organ),
        },
        "organ": organ,
    }


def main():
    date = os.environ.get("SCAN_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for key, (curated, fname, title) in CATEGORIES.items():
        print(f"[{key}] resolving {len(curated)} identities from Enhetsregisteret…",
              file=sys.stderr)
        out = build(curated, title, date)
        path = os.path.join(HERE, fname)
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=2)
        print(f"Wrote {path} ({out['meta']['count']} entities)", file=sys.stderr)


if __name__ == "__main__":
    main()
