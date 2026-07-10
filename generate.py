# -*- coding: utf-8 -*-
"""
PACT — génération autonome du site (scan Bien'ici + filtres + estimation + classement).
Tourne sans dépendance externe (urllib stdlib). Lit template.html, écrit index.html.
Conçu pour être exécuté par GitHub Actions chaque matin.
"""
import json, re, os, sys, time, datetime, urllib.parse, urllib.request

# ----------------------------------------------------------------------------
# 1) ZONES & FILTRES
# ----------------------------------------------------------------------------
ZONES = {"Paris":-7444, "Hauts-de-Seine":-7449, "Seine-Saint-Denis":-7389, "Val-de-Marne":-7458}
MAXPAGES = int(os.environ.get("PACT_MAXPAGES", "6"))   # 6 x 50 = 300 annonces / zone
PAGESIZE = 50

PARIS_CP = {f"750{n:02d}" for n in range(1,21)} | {"75116"}
COSSU = set("92200 92300 92100 92130 92170 92120 92310 92210 92500 92150 92380 92410 92370 92190 92330 92260 92400 92800 94300 94160 94130 94170 94100 94210 94340 93260 93310 95880".split())
CORRECT = set("94220 94120 94700 94230 94270 94110 94250 94140 92240 92320 92340 92220 92110 92600 92700 92250 92140 92290 92160 93100 93170 93500 93400".split())
WHITELIST = PARIS_CP | COSSU | CORRECT

def tier_of(cp):
    if cp in PARIS_CP: return "Paris"
    if cp in COSSU:    return "Cossu"
    return "Correct"

# réseaux de mandataires / brokers à EXCLURE (nom d'agence, minuscule)
BROKER = ["iad","safti","capifrance","capi france","propriétés privées","proprietes privees","proprietes-privees",
 "megagence","méga agence","dr house","effity","efficity","welmo","sextant","optimhome","optim home",
 "noovimo","expertimo","3g immo","la fourmi","mandataire","bsk immo","i particuliers","immo mandataire","le bon agent"]
# mots de description = fonds / cession -> EXCLURE (sauf vente de murs claire)
CESSION = re.compile(r"cession|droit au bail|fonds de commerce|pas de porte|cède\s+(?:le\s+)?bail|céder\s+(?:le\s+)?bail", re.I)
MURS = re.compile(r"\bmurs?\b", re.I)

# ----------------------------------------------------------------------------
# 2) GRILLES (loyer commercial €/m²/an par CP, valeurs résidentielles par CP)
# ----------------------------------------------------------------------------
OBS = {"75003":550,"75004":600,"75005":550,"75010":450,"75012":450,"75013":450,"75014":500,"75017":450,
 "75018":450,"75019":400,"75020":450,"92130":450,"92140":380,"92170":400,"92260":350,"92500":400,
 "92600":380,"92800":420,"93100":300,"94100":350,"94110":300,"94170":400,"94210":350,"94340":350,"94220":380}
def obs_rent(cp):
    if cp in OBS: return OBS[cp]
    return {"75":470,"92":400,"93":300,"94":350,"95":330}.get(cp[:2],400)

RES = {"75003":(11800,37),"75004":(12600,33),"75005":(11800,32),"75010":(9474,33),"75012":(9764,29),
 "75013":(8743,29),"75014":(9325,28),"75017":(10093,29),"75018":(9005,27),"75019":(8123,29),"75020":(8539,28),
 "92130":(7600,27),"92500":(5850,25),"92600":(6207,24),"92170":(6476,26),"92800":(7300,27),"92260":(4600,22),
 "92310":(5809,24),"92140":(6385,24),"94170":(5596,22),"94100":(5700,20),"94210":(5700,20),"94220":(7260,27),
 "94340":(5550,24),"94110":(5140,22),"93100":(6459,23)}
def res_of(cp):
    if cp in RES: return RES[cp]
    px={"75":9000,"92":6200,"93":6000,"94":5800,"95":5000}.get(cp[:2],6000)
    lo={"75":29,"92":24,"93":22,"94":22,"95":20}.get(cp[:2],23)
    return (px,lo)

CONV = {"cour":(1800,0.75,0.90),"cave":(2500,0.55,0.70),"etage":(1300,0.90,1.0),
 "bureaux":(1300,0.90,1.0),"rdc-res":(1600,0.80,0.95),"appart":(350,1.0,1.0),"entrepot":(2000,0.70,0.90)}
SUBLABEL={"cour":"RDC sur cour","cave":"Cave / sous-sol","etage":"Étage (pas de vitrine)",
 "bureaux":"Bureaux","rdc-res":"RDC en résidence","appart":"Déjà un logement","entrepot":"Entrepôt / activité"}

# ----------------------------------------------------------------------------
# 3) OVERRIDES (corrections humaines validées, appliquées si l'annonce revient)
# ----------------------------------------------------------------------------
RENT_OVERRIDE = {  # id -> (type, loyer_annuel)
 "apimo-85601233":("R",14400),"ag750134-497105708":("R",24000),
 "ag941363-419514097":("R",15210),"apimo-85409197":("R",42000)}
EXPO_OVERRIDE = {  # id -> bucket
 "ag750134-481054592":"cour","ag750134-497105708":"cour","hektor-sti-immo-8534":"cour",
 "netty-company56672onq-pro-68":"cour","netty-monparis-pro-115":"cour","immo-facile-58770424":"cour",
 "apimo-87085922":"cave","apimo-86723639":"etage","netty-toctocfr-pro-203":"bureaux",
 "ag941363-508549757":"bureaux","netty-company35363kzj-pro-123":"bureaux",
 "hektor-annecarole-JOINVILLE-LE-PONT-69442":"appart","immo-facile-59635667":"rdc-res"}

# ----------------------------------------------------------------------------
# 4) REGEX classement & loyers
# ----------------------------------------------------------------------------
RC = re.compile(r"fond de cour|sur cours?\b|arri[eè]re[- ]?cour|c(?:œu|oeu)r d.?[îi]lot|rez[- ]de[- ]jardin|cour intérieure|donnant sur cour|calme sur cour|cour arborée|sur une agréable cour", re.I)
RR = re.compile(r"sur rue|vitrine sur rue|rue tr[eè]s? ?passante|rue passante|pignon|à l.?angle|angle de (?:rue|deux)|forte visibilit|belle visibilit|excellente visibilit|fort (?:passage|flux)|passage important|linéaire (?:de )?vitrine|emplacement (?:n.?.?1|premier)|grande vitrine|double vitrine|triple vitrine|art[eè]re (?:commer|passante)|boutique d.?angle", re.I)
RENTREP = re.compile(r"entrep[oô]t|local d.?activit|atelier|stockage|hangar", re.I)
RETAGE  = re.compile(r"1er\s+étage|au\s+1er|\bétage\b", re.I)
RCAVE   = re.compile(r"\bcave\b|studio d.?enregistrement", re.I)
RDCTOK  = re.compile(r"rez[- ]de[- ]chauss|\brdc\b|plain[- ]pied|de plain", re.I)
RENT_RE = re.compile(r"loyer[^0-9]{0,30}?([0-9][0-9 .]{1,9})\s*(?:€|euros)", re.I)
RENTAB_RE = re.compile(r"rentabilit[ée][^0-9]{0,18}?([0-9]{1,2}(?:[.,][0-9])?)\s*%", re.I)
SSREG = re.compile(r"sous[- ]sol|réserve|cave", re.I)

def clean(t): return re.sub(r"<[^>]+>"," ", t or "").replace("\n"," ")

def estimate_rent(cp, surf, desc):
    """retourne (type, loyer_annuel, yield%) — réel si trouvé, sinon estimé."""
    d = desc
    m = RENTAB_RE.search(d)
    mr = RENT_RE.search(d)
    if mr:
        val = int(re.sub(r"[ .]","",mr.group(1)) or 0)
        if 100 <= val <= 6000:      # mensuel probable
            val *= 12
        if 3000 <= val <= 2000000:
            return ("R", val)
    if m:
        return ("A", None, float(m.group(1).replace(",",".")))
    # estimation
    eff = surf
    if SSREG.search(d) and not RDCTOK.search(d):
        eff = surf*0.5
    elif SSREG.search(d):
        eff = surf*0.8
    loyer = round(obs_rent(cp)*eff)
    return ("E", loyer)

def classify(idv, desc, cp, surf, price):
    if idv in EXPO_OVERRIDE: return EXPO_OVERRIDE[idv]
    d = desc
    if RC.search(d): return "cour"
    if RENTREP.search(d): return "entrepot"
    if RCAVE.search(d) and not RDCTOK.search(d): return "cave"
    if RETAGE.search(d) and not RDCTOK.search(d): return "etage"
    return "rue"

def conversion(sub, cp, surf, price):
    reno_m2,decote,occ = CONV[sub]; reno=round(reno_m2*surf); allin=price+reno
    res = RES.get(cp) or res_of(cp)
    if sub=="cave":
        return dict(sub=sub,reno=reno,allin=allin,resale=None,marge=None,margep=None,loyer=None,rdt=None,
            verdict="NON",vtxt="Cave/sous-sol sans lumière naturelle — non transformable en logement (inhabitable).")
    pxm2,loyerm2 = res
    resale=round(pxm2*surf*decote); marge=resale-allin; margep=marge/allin
    loyer=round(loyerm2*12*surf*occ); rdt=loyer/allin
    if margep>=0.12 or rdt>=0.06: v,t="OUI","Coût de revient nettement sous la valeur logement du secteur : conversion a priori rentable."
    elif margep>=0.0: v,t="LIMITE","Marge faible : rentable seulement si travaux maîtrisés et bon prix d'achat négocié."
    else: v,t="NON","Achat + travaux ≥ valeur logement du secteur : conversion non rentable en l'état."
    return dict(sub=sub,reno=reno,allin=allin,resale=resale,marge=marge,margep=round(margep*100),
        loyer=loyer,rdt=round(rdt*1000)/10,verdict=v,vtxt=t)

# ----------------------------------------------------------------------------
# 5) FETCH Bien'ici
# ----------------------------------------------------------------------------
def fetch_zone(zid):
    ads=[]
    for page in range(1, MAXPAGES+1):
        filt={"size":PAGESIZE,"from":(page-1)*PAGESIZE,"filterType":"buy","newProperty":False,
              "propertyType":["shop","premises","office"],"page":page,"sortBy":"publicationDate",
              "sortOrder":"desc","onTheMarket":[True],"zoneIdsByTypes":{"zoneIds":[zid]}}
        url="https://www.bienici.com/realEstateAds.json?filters="+urllib.parse.quote(json.dumps(filt))
        req=urllib.request.Request(url, headers={
            "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
            "Accept":"application/json","Referer":"https://www.bienici.com/recherche/achat"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data=json.load(r)
        except Exception as e:
            sys.stderr.write(f"[warn] zone {zid} page {page}: {e}\n"); break
        batch=data.get("realEstateAds",[])
        if not batch: break
        ads+=batch
        time.sleep(0.5)
    return ads

def process(ad):
    idv=ad.get("id"); cp=str(ad.get("postalCode") or "")
    if cp not in WHITELIST: return None
    surf=ad.get("surfaceArea") or 0; price=ad.get("price") or 0
    if not surf or not price or surf<5 or price<20000: return None
    crd=ad.get("contactRelativeData") or {}
    agency=(crd.get("agencyNameToDisplay") or "").strip()
    al=agency.lower()
    if any(b in al for b in BROKER): return None
    desc=clean(ad.get("description",""))
    if CESSION.search(desc) and not MURS.search(desc): return None
    ppm2=ad.get("pricePerSquareMeter") or round(price/surf)
    ph=(ad.get("photos") or [])
    photo=(ph[0].get("url") if ph else "").split("?")[0] if ph else ""
    bucket=classify(idv,desc,cp,surf,price)
    is_rue=(bucket=="rue")
    # coefficient de loyer selon le type de bien (un entrepôt/une cave se loue bien moins qu'une vitrine)
    FACTOR={"entrepot":0.35,"cave":0.30,"bureaux":0.70,"etage":0.55,"cour":0.85,"rdc-res":0.80,"appart":0.60,"rue":1.0}
    # loyer
    if idv in RENT_OVERRIDE:
        ntyp,loyer=RENT_OVERRIDE[idv]; ny=round(loyer/price*100,1)
    else:
        est=estimate_rent(cp,surf,desc)
        if est[0]=="A":
            ntyp="A"; ny=est[2]; loyer=round(ny/100*price)
        else:
            ntyp,loyer=est[0],est[1]
            if ntyp=="E":
                loyer=round(loyer*FACTOR.get(bucket,1.0))   # ajuste selon le type
            ny=round(loyer/price*100,1)
    improbable=is_rue and ntyp in ("E","E+") and ny>11.5
    conv=None if is_rue else conversion(bucket,cp,surf,price)
    tl={"R":"Loyer réel","A":"Rendement annoncé","E":"Estimé"}.get(ntyp,"Estimé")
    tc={"R":"reel","A":"annonce"}.get(ntyp,"est")
    occ = 1 if re.search(r"loué|occupé|vente occup", desc, re.I) else (0 if re.search(r"\bvide\b|libre|vacant", desc, re.I) else "")
    return {"ny":round(ny,1),"type":ntyp,"tlabel":tl,"tclass":tc,"conf":"Basse","cp":cp,
        "ville":ad.get("city") or cp,"surf":round(surf,2),"price":price,"ppm2":ppm2,"occ":occ,
        "ag":agency,"id":idv,"tier":tier_of(cp),"loyer":loyer,"offre":round(loyer/0.085),
        "zone":("Paris" if cp.startswith("75") else "Petite couronne"),"photo":photo,
        "url":f"https://www.bienici.com/annonce/{idv}","bucket":bucket,"is_rue":is_rue,
        "confirm":False,"improbable":improbable,"sublabel":("" if is_rue else SUBLABEL[bucket]),"conv":conv}

def main():
    seen=set(); rows=[]
    for name,zid in ZONES.items():
        ads=fetch_zone(zid)
        sys.stderr.write(f"[info] {name}: {len(ads)} annonces brutes\n")
        for ad in ads:
            if ad.get("id") in seen: continue
            seen.add(ad.get("id"))
            r=process(ad)
            if r: rows.append(r)
    sys.stderr.write(f"[info] total retenu: {len(rows)}\n")
    if len(rows) < 8:
        sys.stderr.write("[error] trop peu d'annonces — on NE remplace PAS le site existant.\n")
        sys.exit(1)   # garde-fou : ne publie pas un site vide si Bien'ici bloque

    rows.sort(key=lambda x:(0 if x["is_rue"] else 1, x["improbable"], -x["ny"]))
    rue=[x for x in rows if x["is_rue"]]; log=[x for x in rows if not x["is_rue"]]
    VR={"OUI":0,"LIMITE":1,"NON":2,"?":3}
    log.sort(key=lambda x:(VR.get(x["conv"]["verdict"],3), -(x["conv"]["margep"] or -999)))
    n_rue=len(rue); n_log=len(log)
    n_85=sum(1 for x in rue if not x["improbable"] and x["ny"]>=8.5)
    n_sur=sum(1 for x in rue if x["type"] in ("R","A"))
    n_conv=sum(1 for x in log if x["conv"]["verdict"]=="OUI")
    updated=datetime.datetime.now().strftime("%d/%m/%Y à %Hh%M")
    DATA=json.dumps(rows,ensure_ascii=False)
    tpl=open(os.path.join(os.path.dirname(__file__),"template.html"),encoding="utf-8").read()
    html=(tpl.replace("__DATA__",DATA).replace("__NRUE__",str(n_rue)).replace("__NLOG__",str(n_log))
          .replace("__N85__",str(n_85)).replace("__NSUR__",str(n_sur)).replace("__NCONV__",str(n_conv))
          .replace("__UPDATED__",updated))
    open(os.path.join(os.path.dirname(__file__),"index.html"),"w",encoding="utf-8").write(html)
    print(f"OK — rue {n_rue} | logement {n_log} | >=8.5 {n_85} | sur {n_sur} | convOK {n_conv}")

if __name__=="__main__":
    main()
