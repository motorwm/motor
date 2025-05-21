from flask import Flask, request, jsonify
import requests
import numpy as np
from datetime import datetime

app = Flask(__name__)

# ======================
# 🔧 Configuración inicial
# ======================
COEFICIENTES = {
    "intercepto": 0.0035364960,
    "Qentidades_3m": -0.0842042651,
    "deuda_mean_3m": 0.0000318869,
    "nosis_compromiso_mensual": -0.0000001408,
    "nosis_score": -0.0007818815,
    "age": 0.0017059463,
    "sit_max_1m": 0.0125032217,
    "grupo_banco": -0.0150974226,
    "dif_deuda_1_3m": 0.0353815967,
    "pi_monetario": -0.0000001781,
    "flag_hombre": 0.0207322175,
    "region_CAPITAL FEDERAL": -0.0108002476,
    "region_CENTRO": 0.0100787755,
    "region_CUYO": 0.0031393120,
    "region_NEA": 0.0020421952,
    "region_NOA": 0.0032558456,
    "region_SIN INFORMAR": -0.0002924466,
    "region_SUR": 0.0006340789,
}

PROVINCIA_REGION = {
    "CAP. FEDERAL": "region_CAPITAL FEDERAL",
    "BUENOS AIRES": "region_CENTRO",
    "CORDOBA": "region_CENTRO",
    "ENTRE RIOS": "region_CENTRO",
    "SANTA FE": "region_CENTRO",
    "MENDOZA": "region_CUYO",
    "SAN JUAN": "region_CUYO",
    "SAN LUIS": "region_CUYO",
    "LA RIOJA": "region_CUYO",
    "CHACO": "region_NEA",
    "CORRIENTES": "region_NEA",
    "FORMOSA": "region_NEA",
    "MISIONES": "region_NEA",
    "CATAMARCA": "region_NOA",
    "JUJUY": "region_NOA",
    "SALTA": "region_NOA",
    "SANTIAGO DEL ESTERO": "region_NOA",
    "TUCUMAN": "region_NOA",
    "NEUQUEN": "region_SUR",
    "RIO NEGRO": "region_SUR",
    "CHUBUT": "region_SUR",
    "SANTA CRUZ": "region_SUR",
    "TIERRA DEL FUEGO": "region_SUR",
    "LA PAMPA": "region_SUR",
    "SIN INFORMAR": "region_SIN INFORMAR",
    "0": "region_SIN INFORMAR"
}

TABLA_RIESGO = [
    (1, 304, "Out"),
    (305, 598, "Alto"),
    (599, 644, "Medio"),
    (645, 999, "Bajo")
]

CONDICIONES_OFERTA = {
    "Alto": {"plazo_max": 6, "monto_max": 500000, "rci": 0.25},
    "Medio": {"plazo_max": 9, "monto_max": 750000, "rci": 0.30},
    "Bajo": {"plazo_max": 12, "monto_max": 1500000, "rci": 0.35}
}

# ======================
# 🔍 Funciones auxiliares
# ======================
def extraer_valor(nosis_json, nombre_variable):
    variables = nosis_json.get("Contenido", {}).get("Datos", {}).get("Variables", [])
    return float(next((v["Valor"] for v in variables if v["Nombre"] == nombre_variable), 0))

def calcular_edad(birthdate_str):
    nacimiento = datetime.strptime(birthdate_str, "%Y-%m-%d")
    hoy = datetime.today()
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))

def nivel_riesgo(score):
    for minimo, maximo, nivel in TABLA_RIESGO:
        if minimo <= score <= maximo:
            return nivel
    return "Out"

# ======================
# 🚦 Motor de decisión
# ======================
def evaluar_cliente(data):
    cuil = data["cuil"]
    dni = str(cuil)[2:10]
    sexo = data["sexo"]
    birthdate = data["birthdate"]
    edad = calcular_edad(birthdate)

    if edad < 25 or edad > 70:
        return {"rechazado": True, "motivo": "interno", "explicacion": f"Edad fuera de rango permitido: {edad} años"}

    deuda = requests.get(f"https://nwbc2024.pythonanywhere.com/deuda/{cuil}/json", timeout=10).json()
    sit_max_1m = deuda.get("sit_max_1m", 0)

    if sit_max_1m > 1:
        return {"rechazado": True, "motivo": "bcra", "explicacion": f"Situación crediticia > 1 en el último mes: {sit_max_1m}"}

    pyp = requests.get(
        f"https://www.pypdatos.com.ar:469/wayni/rest/serviciospyp/persona/waynimv/57ynbdnr/{dni}/m/json", timeout=10, verify= False
    ).json()
    persona = pyp["RESULTADO"]["persona"]["row"]

    provincia = persona.get("provincia", "SIN INFORMAR").upper()
    region_key = PROVINCIA_REGION.get(provincia, "region_SIN INFORMAR")
    estimador = float(persona.get("estimador", 0)) * 1000

    nosis = requests.get(
        f"https://ws01.nosis.com/rest/variables?usuario=448608&token=914286&documento={dni}&VR=2&format=json", timeout=10
    ).json()
    score = extraer_valor(nosis, "SCO_Vig")
    compromiso = extraer_valor(nosis, "CI_Vig_CompMensual")
    consultas = extraer_valor(nosis, "CO_1m_Finan_Cant") + extraer_valor(nosis, "CO_1m_Banca_Cant")
    referencias = extraer_valor(nosis, "RC_Vig_Cant")

    if consultas > 5:
        return {"rechazado": True, "motivo": "nosis", "explicacion": f"Cantidad de consultas en Nosis > 5: {consultas}"}
    if score < 190:
        return {"rechazado": True, "motivo": "nosis", "explicacion": f"Score Nosis menor a 190: {score}"}
    if referencias > 1:
        return {"rechazado": True, "motivo": "nosis", "explicacion": f"Referencias comerciales mayores a 1: {referencias}"}

    regiones = {k: 0 for k in COEFICIENTES if k.startswith("region_")}
    regiones[region_key] = 1

    variables = {
        "intercepto": 1,
        "Qentidades_3m": deuda.get("Qentidades_3m", 0),
        "deuda_mean_3m": deuda.get("deuda_mean_3m", 0),
        "nosis_compromiso_mensual": compromiso,
        "nosis_score": score,
        "age": edad,
        "sit_max_1m": sit_max_1m,
        "grupo_banco": 0,
        "dif_deuda_1_3m": deuda.get("dif_deuda_1_3m", 0),
        "pi_monetario": estimador,
        "flag_hombre": 1 if sexo == "M" else 0,
        **regiones
    }

    logit = sum(variables[k] * COEFICIENTES[k] for k in COEFICIENTES)
    p = 1-(1 / (1 + np.exp(-logit)))
    score_final = round(p * 1000, 2)
    riesgo = nivel_riesgo(score_final)

    oferta = CONDICIONES_OFERTA.get(riesgo)
    cuota_max = round(oferta["rci"] * estimador, 2) if oferta else 0

    return {
        "rechazado": False,
        "motivo": "aprobado",
        "score": score_final,
        "probabilidad_default": round(p, 6),
        "nivel_riesgo": riesgo,
        "region": region_key,
        "edad": edad,
        "plazo_max": oferta["plazo_max"] if oferta else 0,
        "monto_max": oferta["monto_max"] if oferta else 0,
        "rci": oferta["rci"] if oferta else 0,
        "cuota_max": cuota_max
    }

# ======================
# 🚀 Endpoint Flask
# ======================
@app.route("/evaluar", methods=["POST"])
def endpoint_evaluar():
    try:
        data = request.get_json()
        resultado = evaluar_cliente(data)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)