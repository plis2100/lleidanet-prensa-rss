import os
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


URL_BASE = "https://investors.lleida.net"
URL_PRENSA = "https://investors.lleida.net/es/prensa"

ARCHIVO_RSS = Path("rss.xml")
ZONA_HORARIA = ZoneInfo("Europe/Madrid")
MAX_ARTICULOS = 3000

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
    "Referer": URL_BASE + "/",
}


def dentro_del_horario():
    """
    Ejecuciones automáticas:
    - De lunes a viernes.
    - Cada hora.
    - Desde las 07:00 hasta las 19:59.
    - Hora peninsular española.

    Los lanzamientos manuales funcionan cualquier día y hora.
    """
    evento = os.environ.get("GITHUB_EVENT_NAME", "")

    if evento == "workflow_dispatch":
        print("Ejecución manual: se ignora el límite horario.")
        return True

    ahora = datetime.now(ZONA_HORARIA)
    print(f"Hora española: {ahora:%Y-%m-%d %H:%M:%S %Z}")

    if ahora.weekday() >= 5:
        print("Fin de semana: no se actualiza el RSS.")
        return False

    if not 7 <= ahora.hour <= 19:
        print("Fuera del horario permitido: 07:00-19:59.")
        return False

    return True


def limpiar_texto(valor):
    if valor is None:
        return ""

    return " ".join(str(valor).split()).strip()


def normalizar_url(url):
    url = limpiar_texto(url)

    if not url:
        return ""

    return urljoin(URL_BASE, url).split("#")[0]


def obtener_prid(url):
    try:
        consulta = parse_qs(urlparse(url).query)
        valores = consulta.get("prid", [])

        if valores and str(valores[0]).isdigit():
            return str(valores[0])

    except (ValueError, TypeError):
        pass

    return ""


def crear_url_noticia(prid):
    return URL_PRENSA + "?" + urlencode({"prid": prid})


def descargar(url):
    ultimo_error = None

    for intento in range(1, 4):
        try:
            respuesta = requests.get(
                url,
                headers=CABECERAS,
                timeout=45,
                allow_redirects=True,
            )
            respuesta.raise_for_status()

            if not respuesta.text.strip():
                raise RuntimeError("La página se descargó vacía.")

            print(
                f"Descargada {url}: "
                f"{len(respuesta.content)} bytes."
            )
            return respuesta.text

        except Exception as error:
            ultimo_error = error
            print(
                f"Intento {intento}/3 fallido para {url}: "
                f"{error}"
            )

    raise RuntimeError(
        f"No se pudo descargar {url}: {ultimo_error}"
    )


def convertir_fecha(valor):
    valor = limpiar_texto(valor).lower()

    meses = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }

    patron = (
        r"\b(\d{1,2})\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"(?:\s+de)?\s+(\d{4})\b"
    )

    coincidencia = re.search(
        patron,
        valor,
        flags=re.IGNORECASE,
    )

    if coincidencia:
        dia = int(coincidencia.group(1))
        mes = meses[coincidencia.group(2).lower()]
        anio = int(coincidencia.group(3))

        try:
            fecha = datetime(
                anio,
                mes,
                dia,
                12,
                0,
                tzinfo=ZONA_HORARIA,
            )

            return format_datetime(
                fecha.astimezone(timezone.utc)
            )

        except ValueError:
            pass

    coincidencia = re.search(
        r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
        valor,
    )

    if coincidencia:
        dia, mes, anio = map(int, coincidencia.groups())

        try:
            fecha = datetime(
                anio,
                mes,
                dia,
                12,
                0,
                tzinfo=ZONA_HORARIA,
            )

            return format_datetime(
                fecha.astimezone(timezone.utc)
            )

        except ValueError:
            pass

    return format_datetime(datetime.now(timezone.utc))


def buscar_fecha(contenedor):
    contenido = contenedor.get_text(" ", strip=True)

    patron_texto = (
        r"\b\d{1,2}\s+"
        r"(?:enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"(?:\s+de)?\s+\d{4}\b"
    )

    coincidencia = re.search(
        patron_texto,
        contenido,
        flags=re.IGNORECASE,
    )

    if coincidencia:
        return convertir_fecha(coincidencia.group(0))

    coincidencia = re.search(
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        contenido,
    )

    if coincidencia:
        return convertir_fecha(coincidencia.group(0))

    return format_datetime(datetime.now(timezone.utc))


def buscar_contenedor(enlace_html):
    contenedor = enlace_html

    for _ in range(8):
        if contenedor.parent is None:
            break

        contenedor = contenedor.parent
        contenido = contenedor.get_text(" ", strip=True)

        tiene_fecha = bool(
            re.search(
                r"\b\d{1,2}(?:/\d{1,2}/|\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]+"
                r"(?:\s+de)?\s+)\d{4}\b",
                contenido,
                flags=re.IGNORECASE,
            )
        )

        noticias = {
            obtener_prid(normalizar_url(enlace.get("href")))
            for enlace in contenedor.find_all("a", href=True)
            if obtener_prid(
                normalizar_url(enlace.get("href"))
            )
        }

        if tiene_fecha and len(noticias) <= 1:
            return contenedor

    return enlace_html.parent or enlace_html


def obtener_titulo(enlace_html, contenedor):
    titulo = limpiar_texto(
        enlace_html.get_text(" ", strip=True)
    )

    genericos = {
        "ver noticia",
        "leer más",
        "descargar",
        "notas de prensa",
    }

    if titulo.lower() in genericos:
        titulo = ""

    if 15 <= len(titulo) <= 500:
        return titulo

    for selector in (
        "h1",
        "h2",
        "h3",
        "h4",
        ".title",
        ".titulo",
        ".news-title",
    ):
        elemento = contenedor.select_one(selector)

        if elemento:
            titulo = limpiar_texto(
                elemento.get_text(" ", strip=True)
            )

            if (
                15 <= len(titulo) <= 500
                and titulo.lower() not in genericos
            ):
                return titulo

    return ""


def obtener_resumen(contenedor, titulo):
    candidatos = []

    for elemento in contenedor.find_all(
        ["p", "div", "span"],
    ):
        contenido = limpiar_texto(
            elemento.get_text(" ", strip=True)
        )

        if not contenido or contenido == titulo:
            continue

        if contenido.lower() in (
            "ver noticia",
            "leer más",
            "descargar",
        ):
            continue

        if re.fullmatch(
            r"\d{1,2}(?:/\d{1,2}/|\s+[A-Za-zÁÉÍÓÚáéíóúñÑ]+"
            r"(?:\s+de)?\s+)\d{4}",
            contenido,
            flags=re.IGNORECASE,
        ):
            continue

        if 30 <= len(contenido) <= 1200:
            candidatos.append(contenido)

    if candidatos:
        candidatos.sort(key=len)
        return candidatos[0]

    return ""


def obtener_imagen(contenedor):
    imagen = contenedor.find("img")

    if not imagen:
        return ""

    for atributo in (
        "data-src",
        "data-lazy-src",
        "data-original",
        "src",
    ):
        url = normalizar_url(imagen.get(atributo))

        if url and not url.startswith("data:"):
            return url

    srcset = limpiar_texto(
        imagen.get("data-srcset")
        or imagen.get("srcset")
    )

    if srcset:
        primera = srcset.split(",")[0].strip().split(" ")[0]
        return normalizar_url(primera)

    return ""


def extraer_noticias():
    html = descargar(URL_PRENSA)
    sopa = BeautifulSoup(html, "html.parser")

    articulos = []
    vistos = set()

    for enlace_html in sopa.find_all("a", href=True):
        url_original = normalizar_url(enlace_html.get("href"))
        prid = obtener_prid(url_original)

        if not prid or prid in vistos:
            continue

        contenedor = buscar_contenedor(enlace_html)
        titulo = obtener_titulo(enlace_html, contenedor)

        if not titulo:
            continue

        enlace = crear_url_noticia(prid)
        fecha = buscar_fecha(contenedor)
        resumen = obtener_resumen(contenedor, titulo)
        imagen = obtener_imagen(contenedor)

        descripcion = ""

        if resumen:
            descripcion += f"<p>{resumen}</p>"

        descripcion += (
            f"<p><strong>Identificador:</strong> {prid}</p>"
            f'<p><a href="{enlace}">'
            f"Leer la noticia completa en Lleida.net"
            f"</a></p>"
        )

        articulos.append(
            {
                "title": titulo,
                "link": enlace,
                "guid": f"lleidanet-prensa-{prid}",
                "pubDate": fecha,
                "description": descripcion,
                "author": "Lleida.net",
                "categories": [
                    "Lleida.net",
                    "Notas de prensa",
                    "BME Growth",
                ],
                "image": imagen,
            }
        )

        vistos.add(prid)

    print(
        f"Noticias únicas localizadas: {len(articulos)}"
    )

    return articulos


def leer_articulos_anteriores():
    if not ARCHIVO_RSS.exists():
        return []

    try:
        raiz = ET.parse(ARCHIVO_RSS).getroot()
    except ET.ParseError:
        print("El RSS anterior no es válido; se reconstruirá.")
        return []

    articulos = []

    for item in raiz.findall("./channel/item"):
        categorias = [
            limpiar_texto(elemento.text)
            for elemento in item.findall("category")
            if limpiar_texto(elemento.text)
        ]

        enclosure = item.find("enclosure")
        imagen = ""

        if enclosure is not None:
            imagen = limpiar_texto(enclosure.get("url"))

        articulos.append(
            {
                "title": limpiar_texto(
                    item.findtext("title")
                ),
                "link": limpiar_texto(
                    item.findtext("link")
                ),
                "guid": limpiar_texto(
                    item.findtext("guid")
                ),
                "pubDate": limpiar_texto(
                    item.findtext("pubDate")
                ),
                "description": limpiar_texto(
                    item.findtext("description")
                ),
                "author": limpiar_texto(
                    item.findtext("author")
                ),
                "categories": categorias,
                "image": imagen,
            }
        )

    print(
        f"Noticias recuperadas del RSS anterior: "
        f"{len(articulos)}"
    )

    return articulos


def clave_articulo(articulo):
    guid = limpiar_texto(articulo.get("guid"))

    if guid:
        return guid.lower()

    enlace = limpiar_texto(articulo.get("link"))

    if enlace:
        prid = obtener_prid(enlace)

        if prid:
            return f"lleidanet-prensa-{prid}"

        return enlace.lower()

    return limpiar_texto(articulo.get("title")).lower()


def fecha_ordenacion(articulo):
    try:
        fecha = parsedate_to_datetime(
            articulo["pubDate"]
        )

        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)

        return fecha.timestamp()

    except (
        TypeError,
        ValueError,
        OverflowError,
        KeyError,
    ):
        return 0


def combinar_articulos(nuevos, anteriores):
    nuevos.sort(
        key=fecha_ordenacion,
        reverse=True,
    )

    resultado = []
    vistos = set()

    for articulo in nuevos + anteriores:
        clave = clave_articulo(articulo)

        if not clave or clave in vistos:
            continue

        vistos.add(clave)
        resultado.append(articulo)

        if len(resultado) >= MAX_ARTICULOS:
            break

    return resultado


def añadir_texto(padre, etiqueta, valor):
    elemento = ET.SubElement(padre, etiqueta)
    elemento.text = limpiar_texto(valor)
    return elemento


def crear_rss(articulos):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
        },
    )

    canal = ET.SubElement(rss, "channel")

    añadir_texto(
        canal,
        "title",
        "Lleida.net — Notas de prensa",
    )
    añadir_texto(
        canal,
        "link",
        URL_PRENSA,
    )
    añadir_texto(
        canal,
        "description",
        (
            "Notas de prensa y noticias publicadas "
            "por Lleida.net para inversores."
        ),
    )
    añadir_texto(canal, "language", "es")
    añadir_texto(
        canal,
        "lastBuildDate",
        format_datetime(datetime.now(timezone.utc)),
    )
    añadir_texto(
        canal,
        "generator",
        "GitHub Actions RSS Generator",
    )

    atom = ET.SubElement(
        canal,
        "{http://www.w3.org/2005/Atom}link",
    )
    atom.set(
        "href",
        (
            "https://raw.githubusercontent.com/"
            "plis2100/lleidanet-prensa-rss/main/rss.xml"
        ),
    )
    atom.set("rel", "self")
    atom.set("type", "application/rss+xml")

    for articulo in articulos:
        item = ET.SubElement(canal, "item")

        añadir_texto(
            item,
            "title",
            articulo["title"],
        )
        añadir_texto(
            item,
            "link",
            articulo["link"],
        )

        guid = añadir_texto(
            item,
            "guid",
            articulo["guid"],
        )
        guid.set("isPermaLink", "false")

        añadir_texto(
            item,
            "pubDate",
            articulo["pubDate"],
        )
        añadir_texto(
            item,
            "description",
            articulo["description"],
        )
        añadir_texto(
            item,
            "author",
            articulo["author"],
        )

        for categoria in articulo["categories"]:
            añadir_texto(
                item,
                "category",
                categoria,
            )

        if articulo["image"]:
            enclosure = ET.SubElement(
                item,
                "enclosure",
            )
            enclosure.set("url", articulo["image"])
            enclosure.set("type", "image/jpeg")

    arbol = ET.ElementTree(rss)
    ET.indent(arbol, space="  ")

    temporal = ARCHIVO_RSS.with_suffix(".xml.tmp")

    arbol.write(
        temporal,
        encoding="utf-8",
        xml_declaration=True,
    )

    temporal.replace(ARCHIVO_RSS)


def main():
    if not dentro_del_horario():
        return

    nuevos = extraer_noticias()
    anteriores = leer_articulos_anteriores()

    if not nuevos and not anteriores:
        raise RuntimeError(
            "Lleida.net no devolvió ninguna noticia y "
            "tampoco existe un RSS anterior."
        )

    if not nuevos and anteriores:
        print(
            "AVISO: no se localizaron noticias nuevas. "
            "Se conservará el RSS anterior."
        )

    articulos = combinar_articulos(
        nuevos,
        anteriores,
    )

    if not articulos:
        raise RuntimeError(
            "No hay artículos para escribir en el RSS."
        )

    crear_rss(articulos)

    print(
        f"RSS creado correctamente con "
        f"{len(articulos)} noticias."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )
        raise
