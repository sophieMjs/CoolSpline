from flask import Flask, render_template, request, jsonify
import requests
from datetime import datetime
import numpy as np
from scipy.interpolate import CubicSpline

app = Flask(__name__)

# Bogotá, Colombia
BOGOTA_LAT = 20.9674
BOGOTA_LON = -89.5926
BOGOTA_TZ = "America/Merida"


# Coeficientes ilustrativos del acoplamiento térmico.
# No son constantes universales: deben calibrarse con datos reales
# de cada centro de datos.
FACTOR_TEMPERATURA_EXTERIOR = 0.10  # °C equivalentes / °C exterior
FACTOR_RADIACION = 0.30             # °C equivalentes / (kW/m²)


def spline_natural_manual(x, a):
    """Construye un spline cúbico natural resolviendo el sistema tridiagonal."""
    x = np.asarray(x, dtype=float)
    a = np.asarray(a, dtype=float)

    if len(x) < 2:
        raise ValueError("Se necesitan al menos 2 nodos.")
    if len(x) != len(a):
        raise ValueError("x y a deben tener la misma cantidad de valores.")
    if np.any(np.diff(x) <= 0):
        raise ValueError("Los nodos deben ser estrictamente crecientes.")

    n = len(x) - 1
    h = np.diff(x)
    A = np.zeros((n + 1, n + 1))
    r = np.zeros(n + 1)

    # Condiciones naturales: S''(x0) = S''(xn) = 0.
    A[0, 0] = 1.0
    A[n, n] = 1.0

    for j in range(1, n):
        A[j, j - 1] = h[j - 1]
        A[j, j] = 2.0 * (h[j - 1] + h[j])
        A[j, j + 1] = h[j]
        r[j] = (
            3.0 * (a[j + 1] - a[j]) / h[j]
            - 3.0 * (a[j] - a[j - 1]) / h[j - 1]
        )

    c = np.linalg.solve(A, r)
    b = (a[1:] - a[:-1]) / h - h * (2.0 * c[:-1] + c[1:]) / 3.0
    d = (c[1:] - c[:-1]) / (3.0 * h)
    return h, A, r, c, b, d


def evaluar(xq, x, a, b, c, d):
    """Evalúa el spline manual por tramos."""
    xq = np.asarray(xq, dtype=float)
    j = np.clip(np.searchsorted(x, xq, side="right") - 1, 0, len(x) - 2)
    s = xq - x[j]
    return a[j] + b[j] * s + c[j] * s**2 + d[j] * s**3


def rmse(predicho, real):
    return float(np.sqrt(np.mean((np.asarray(predicho) - np.asarray(real)) ** 2)))


def formato_hora(minuto_desde_inicio, hora_inicio):
    total = int(round(float(hora_inicio) * 60)) + int(round(float(minuto_desde_inicio)))
    if total == 24 * 60:
        return "24:00"
    hora = (total // 60) % 24
    minuto = total % 60
    return f"{hora:02d}:{minuto:02d}"


def detectar_inicios(mask):
    """Devuelve el primer índice de cada episodio, incluido el índice cero."""
    mask = np.asarray(mask, dtype=bool)
    if mask.size == 0:
        return np.array([], dtype=int)
    anterior = np.r_[False, mask[:-1]]
    return np.flatnonzero(mask & ~anterior)


def intervalos_desde_mascara(mask, minutos, hora_inicio):
    """Agrupa muestras consecutivas que cumplen una condición."""
    mask = np.asarray(mask, dtype=bool)
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return []

    grupos = np.split(indices, np.where(np.diff(indices) != 1)[0] + 1)
    intervalos = []
    for grupo in grupos:
        inicio = int(grupo[0])
        fin = int(grupo[-1])
        intervalos.append({
            "inicio": formato_hora(minutos[inicio], hora_inicio),
            "fin": formato_hora(minutos[fin], hora_inicio),
            "cantidad_minutos": int(len(grupo))
        })
    return intervalos


def obtener_pronostico_api(latitud, longitud, fecha_pronostico, hora_min, hora_max):
    """Consulta Open-Meteo y devuelve las variables horarias necesarias."""
    url = "https://api.open-meteo.com/v1/forecast"
    parametros = {
        "latitude": latitud,
        "longitude": longitud,
        "hourly": "temperature_2m,shortwave_radiation",
        "forecast_days": 16,
        "timezone": BOGOTA_TZ
    }

    try:
        respuesta = requests.get(url, params=parametros, timeout=30)
        respuesta.raise_for_status()
        meteorologia = respuesta.json()
    except requests.RequestException as exc:
        raise ValueError(
            "No fue posible consultar Open-Meteo. Verifica tu conexión e inténtalo de nuevo."
        ) from exc

    try:
        hourly = meteorologia["hourly"]
        fechas = hourly["time"]
        temperaturas = np.asarray(hourly["temperature_2m"], dtype=float)
        radiacion = np.asarray(hourly["shortwave_radiation"], dtype=float)
        zona = meteorologia.get("timezone", BOGOTA_TZ)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Open-Meteo no devolvió las variables meteorológicas esperadas.") from exc

    if len(fechas) < 2 or len(temperaturas) != len(fechas) or len(radiacion) != len(fechas):
        raise ValueError("La respuesta meteorológica está incompleta.")

    fecha_base = datetime.strptime(fecha_pronostico, "%Y-%m-%d").date()
    horas_api = []
    for fecha_hora in fechas:
        instante = datetime.fromisoformat(fecha_hora)
        diferencia = (instante.date() - fecha_base).days
        horas_api.append(diferencia * 24 + instante.hour + instante.minute / 60)

    horas_api = np.asarray(horas_api, dtype=float)
    mascara = (
        (horas_api >= np.floor(hora_min))
        & (horas_api <= np.ceil(hora_max))
    )

    horas = horas_api[mascara]
    temp_ext = temperaturas[mascara]
    rad = radiacion[mascara]

    if len(horas) < 2:
        raise ValueError(
            "Open-Meteo no tiene suficientes horas disponibles para la fecha seleccionada."
        )
    if np.any(np.diff(horas) <= 0):
        raise ValueError("Los horarios recibidos de Open-Meteo no son válidos.")
    if horas[0] > hora_min or horas[-1] < hora_max:
        raise ValueError(
            "El pronóstico de Open-Meteo no cubre todo el intervalo requerido. "
            "Prueba con otra fecha dentro de los próximos días."
        )
    if not np.all(np.isfinite(temp_ext)) or not np.all(np.isfinite(rad)):
        raise ValueError("El pronóstico contiene valores meteorológicos no válidos.")

    return horas, temp_ext, rad, zona


def simular_control_ventiladores(t, temperatura, derivada_h, temp_critica, umbral_min, potencia_kw):
    """Comparación energética abierta sobre una MISMA trayectoria térmica fija.

    No simula el efecto de la refrigeración sobre la temperatura: solo estima
    la potencia requerida por dos políticas de mando ilustrativas.
    """
    if not np.isfinite(potencia_kw) or potencia_kw <= 0:
        raise ValueError("La potencia nominal debe ser mayor que cero.")
    if temp_critica <= 20:
        raise ValueError("Para la simulación de ventiladores, la temperatura crítica debe superar 20 °C.")
    # Histéresis de 1 °C para evitar conmutación excesiva.
    encendido = False
    onoff = np.zeros(len(t), dtype=float)
    continuo = np.zeros(len(t), dtype=float)
    t_inicio = temp_critica - 1.0
    t_parada = temp_critica - 2.0
    t_baja = temp_critica - 5.0
    # Se limita la derivada positiva a 2 veces el umbral para normalizarla.
    umbral_h = umbral_min * 60.0
    for i, (temp, tasa) in enumerate(zip(temperatura, derivada_h)):
        if not encendido and temp >= t_inicio:
            encendido = True
        elif encendido and temp <= t_parada:
            encendido = False
        onoff[i] = 1.0 if encendido else 0.0
        # Control gradual: 75% temperatura, 25% tasa de calentamiento.
        term_temp = float(np.clip((temp - t_baja) / (temp_critica - t_baja), 0, 1))
        term_tasa = float(np.clip(max(0, tasa) / (2 * umbral_h), 0, 1))
        demanda = np.clip(0.75 * term_temp + 0.25 * term_tasa, 0, 1)
        continuo[i] = 0.0 if demanda == 0 else 0.25 + 0.75 * demanda
    # Ley de afinidad aproximada P/Pnom = v³; muestreo de intervalo por la izquierda.
    dt = np.diff(t)
    e_onoff = float(np.sum(potencia_kw * onoff[:-1] * dt))
    e_continuo = float(np.sum(potencia_kw * continuo[:-1] ** 3 * dt))
    ahorro = float(100 * (e_onoff - e_continuo) / e_onoff) if e_onoff > 0 else None
    return {
        "potencia_nominal_kw": potencia_kw,
        "energia_todonada_kwh": e_onoff,
        "energia_continua_kwh": e_continuo,
        "ahorro_porcentaje": ahorro,
        "diferencia_kwh": e_onoff - e_continuo,
        "velocidad_todonada": onoff.tolist(),
        "velocidad_continua": continuo.tolist(),
        "potencia_todonada_kw": (potencia_kw * onoff).tolist(),
        "potencia_continua_kw": (potencia_kw * continuo**3).tolist(),
        "encendido_c": t_inicio,
        "apagado_c": t_parada,
        "modelo": "",
    }


def detectar_anomalias_causales(x, y, tolerancia_c):
    """Pronóstico de la siguiente medición con solo el historial anterior.

    Se requieren 4 puntos previos; predicción de un paso mediante extensión
    del spline natural previo. Es EXTRAPOLACIÓN, no interpolación, y su
    incertidumbre crece con el horizonte temporal.
    """
    if not np.isfinite(tolerancia_c) or tolerancia_c <= 0:
        raise ValueError("El umbral de anomalía debe ser mayor que cero.")
    registros = []
    for i in range(4, len(x)):
        # Últimos cuatro puntos disponibles antes de la medición actual.
        previo = CubicSpline(x[i-4:i], y[i-4:i], bc_type="natural")
        pred = float(previo(x[i]))
        error = float(y[i] - pred)
        registros.append({
            "hora": float(x[i]), "hora_texto": formato_hora(0, x[i]),
            "medida": float(y[i]), "esperada": pred, "residuo": error,
            "anomalia": bool(abs(error) > tolerancia_c),
            "tipo": "Temperatura superior a la prevista" if error > 0 else "Temperatura inferior a la prevista",
        })
    return {
        "umbral_c": float(tolerancia_c),
        "evaluaciones": len(registros),
        "cantidad": sum(int(r["anomalia"]) for r in registros),
        "registros": registros,
        "metodo": "",
    }


def analizar(
    x, y, umbral_calentamiento, temperatura_critica, tiempo_anticipacion,
    modo_pronostico, amplitud_perturbacion, hora_pico_perturbacion,
    latitud, longitud, fecha_pronostico, potencia_ventilador_kw=1.0, umbral_anomalia_c=2.0
):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    if len(x) != len(y):
        raise ValueError("La cantidad de horas y temperaturas no coincide.")
    if len(x) < 3:
        raise ValueError("Se necesitan al menos 3 mediciones para las validaciones.")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("Existen datos vacíos, NaN o infinitos.")
    orden = np.argsort(x)
    x, y = x[orden], y[orden]
    if np.any(np.diff(x) <= 0):
        raise ValueError("Las horas deben ser únicas y crecientes.")

    if not np.isfinite(umbral_calentamiento) or umbral_calentamiento <= 0:
        raise ValueError("El umbral de calentamiento debe ser mayor que cero.")
    if not np.isfinite(temperatura_critica):
        raise ValueError("La temperatura crítica no es válida.")
    if not np.isfinite(tiempo_anticipacion) or tiempo_anticipacion < 0:
        raise ValueError("La anticipación debe ser igual o mayor que cero.")
    if modo_pronostico not in ("simulado", "api"):
        raise ValueError("Modo de pronóstico inválido: usa simulado o api.")

    tau = tiempo_anticipacion / 60.0
    n = len(x)

    # 1. Spline natural manual y verificación contra SciPy.
    h, A, r, c, b, d = spline_natural_manual(x, y)
    a = y[:-1]
    cs = CubicSpline(x, y, bc_type="natural")

    xx = np.linspace(x[0], x[-1], 2001)
    diferencia_manual_scipy = float(
        np.max(np.abs(evaluar(xx, x, a, b, c, d) - cs(xx)))
    )

    # 2. Hold-out: 80% entrenamiento y 20% prueba, seleccionando nodos interiores.
    k = min(n - 2, max(1, round(0.2 * n)))
    test = np.unique(np.linspace(1, n - 2, k).round().astype(int))
    train = np.asarray([i for i in range(n) if i not in test], dtype=int)

    cs_train = CubicSpline(x[train], y[train], bc_type="natural")
    pred_spline = cs_train(x[test])
    pred_lineal = np.interp(x[test], x[train], y[train])

    holdout_detalles = []
    for pos, idx in enumerate(test):
        holdout_detalles.append({
            "hora": float(x[idx]),
            "real": float(y[idx]),
            "spline": float(pred_spline[pos]),
            "lineal": float(pred_lineal[pos]),
            "error_spline": float(pred_spline[pos] - y[idx]),
            "error_lineal": float(pred_lineal[pos] - y[idx])
        })

    # 3. LOO interior: se excluye uno por uno cada nodo interior.
    errores_loo_spline = []
    errores_loo_lineal = []
    loo_detalles = []

    for i in range(1, n - 1):
        train_loo = np.asarray([j for j in range(n) if j != i], dtype=int)
        spline_loo = CubicSpline(x[train_loo], y[train_loo], bc_type="natural")
        pred_s = float(spline_loo(x[i]))
        pred_l = float(np.interp(x[i], x[train_loo], y[train_loo]))
        err_s = pred_s - float(y[i])
        err_l = pred_l - float(y[i])

        errores_loo_spline.append(err_s)
        errores_loo_lineal.append(err_l)
        loo_detalles.append({
            "hora": float(x[i]),
            "real": float(y[i]),
            "spline": pred_s,
            "lineal": pred_l,
            "error_spline": float(err_s),
            "error_lineal": float(err_l)
        })

    rmse_loo_spline = float(np.sqrt(np.mean(np.square(errores_loo_spline))))
    rmse_loo_lineal = float(np.sqrt(np.mean(np.square(errores_loo_lineal))))

    # 4. Grilla de evaluación: un punto por minuto, incluyendo ambos extremos.
    resolucion_minutos = 1
    duracion_minutos = int(round((x[-1] - x[0]) * 60))
    minutos = np.arange(0, duracion_minutos + 1, resolucion_minutos)
    if minutos[-1] != duracion_minutos:
        minutos = np.append(minutos, duracion_minutos)
    t = x[0] + minutos / 60.0

    T = cs(t)
    dT_h = cs.derivative(1)(t)
    dT_min = dT_h / 60.0
    lineal = np.interp(t, x, y)

    # 5. Fuente de perturbación: simulación senoidal o Open-Meteo.
    meteo_info = {
        "modo": modo_pronostico,
        "fuente": "Modelo senoidal simulado" if modo_pronostico == "simulado" else "Open-Meteo",
        "ubicacion": "Merida, Yucatán" if modo_pronostico == "api" else "No aplica (simulación)",
        "fecha": fecha_pronostico if modo_pronostico == "api" else None,
        "latitud": float(latitud) if modo_pronostico == "api" else None,
        "longitud": float(longitud) if modo_pronostico == "api" else None,
        "zona_horaria": BOGOTA_TZ if modo_pronostico == "api" else None
    }

    horas_meteo = np.array([], dtype=float)
    temp_exterior = np.array([], dtype=float)
    rad_solar = np.array([], dtype=float)
    perturbacion_meteo = np.array([], dtype=float)

    if modo_pronostico == "simulado":
        A_p = float(amplitud_perturbacion)
        h_pico = float(hora_pico_perturbacion)
        if not np.isfinite(A_p) or A_p < 0:
            raise ValueError("La amplitud de la simulación debe ser igual o mayor que cero.")
        if not np.isfinite(h_pico) or not 0 <= h_pico <= 24:
            raise ValueError("La hora del pico debe estar entre 0 y 24.")

        def P(tt):
            return A_p * 0.5 * (1.0 + np.cos(2.0 * np.pi * (tt - h_pico) / 24.0))

        def dP(tt):
            return -A_p * np.pi / 24.0 * np.sin(2.0 * np.pi * (tt - h_pico) / 24.0)

        perturbacion = P(t)
        derivada_perturbacion = dP(t)
        meteo_info.update({
            "amplitud": A_p,
            "hora_pico": h_pico,
            "descripcion": "Perturbación senoidal ilustrativa"
        })

    else:
        latitud = float(latitud)
        longitud = float(longitud)
        if not (-90 <= latitud <= 90 and -180 <= longitud <= 180):
            raise ValueError("Las coordenadas geográficas están fuera de rango.")
        if not fecha_pronostico:
            raise ValueError("Selecciona una fecha para consultar Open-Meteo.")

        # La consulta cubre el intervalo del spline. El pronóstico anticipado
        # solo se evalúa dentro de ese mismo intervalo.
        horas_meteo, temp_exterior, rad_solar, zona = obtener_pronostico_api(
            latitud, longitud, fecha_pronostico, x[0], x[-1]
        )

        temp_referencia = float(np.mean(temp_exterior))
        radiacion_kw = rad_solar / 1000.0

        # P(t) se expresa como una perturbación equivalente en °C.
        # Los factores son parámetros ilustrativos que requieren calibración.
        perturbacion_meteo = (
            FACTOR_TEMPERATURA_EXTERIOR * (temp_exterior - temp_referencia)
            + FACTOR_RADIACION * radiacion_kw
        )
        derivada_meteo = np.gradient(perturbacion_meteo, horas_meteo)

        perturbacion = np.interp(t, horas_meteo, perturbacion_meteo)
        derivada_perturbacion = np.interp(t, horas_meteo, derivada_meteo)

        meteo_info.update({
            "zona_horaria": zona,
            "temperatura_exterior_media": float(np.mean(temp_exterior)),
            "temperatura_exterior_min": float(np.min(temp_exterior)),
            "temperatura_exterior_max": float(np.max(temp_exterior)),
            "radiacion_max": float(np.max(rad_solar)),
            "perturbacion_media": float(np.mean(perturbacion_meteo)),
            "factor_temperatura_exterior": FACTOR_TEMPERATURA_EXTERIOR,
            "factor_radiacion": FACTOR_RADIACION,
            "descripcion": "Temperatura exterior y radiación solar de Open-Meteo"
        })

    # 6. Temperatura y derivada ajustadas.
    Tc = T + perturbacion
    dTc = dT_h + derivada_perturbacion

    control_energia = simular_control_ventiladores(
        t, Tc, dTc, temperatura_critica, umbral_calentamiento, float(potencia_ventilador_kw)
    )
    anomalias = detectar_anomalias_causales(x, y, float(umbral_anomalia_c))

    # 7. Pronóstico con anticipación.
    # Solo se predicen instantes para los que t + tau sigue dentro de [x0, xn].
    mascara_prediccion = (t + tau) <= (x[-1] + 1e-10)
    t_decision = t[mascara_prediccion]
    t_futuro = t_decision + tau
    perturbacion_futura = np.interp(t_futuro, t, perturbacion)
    derivada_futura = np.interp(t_futuro, t, derivada_perturbacion)

    Tpred = cs(t_futuro) + perturbacion_futura
    derivada_predicha_h = cs.derivative(1)(t_futuro) + derivada_futura
    derivada_predicha_min = derivada_predicha_h / 60.0

    # 8. Detectores de calentamiento: base, ajustado y preactivo.
    sobre_base = dT_min > umbral_calentamiento
    sobre_ajustado = (dTc / 60.0) > umbral_calentamiento
    sobre_preactivo = derivada_predicha_min > umbral_calentamiento

    inicios_base = detectar_inicios(sobre_base)
    inicios_ajustado = detectar_inicios(sobre_ajustado)
    inicios_preactivo = detectar_inicios(sobre_preactivo)

    alertas_base = [formato_hora(minutos[i], x[0]) for i in inicios_base]
    alertas_ajustadas = [formato_hora(minutos[i], x[0]) for i in inicios_ajustado]
    alertas_preactivas = [
        formato_hora(t_decision[i] * 60.0 - x[0] * 60.0, x[0])
        for i in inicios_preactivo
    ]

    # 9. Minutos por encima del límite: se registran explícitamente.
    sobre_temperatura = T > temperatura_critica
    indices_calientes = np.flatnonzero(sobre_temperatura)
    minutos_calientes = [
        formato_hora(minutos[i], x[0]) for i in indices_calientes
    ]
    intervalos_calientes = intervalos_desde_mascara(
        sobre_temperatura, minutos, x[0]
    )

    sobre_temperatura_ajustada = Tc > temperatura_critica
    intervalos_ajustados = intervalos_desde_mascara(
        sobre_temperatura_ajustada, minutos, x[0]
    )

    # 10. Picos.
    indice_pico_base = int(np.argmax(T))
    indice_pico_ajustado = int(np.argmax(Tc))
    pico_base = formato_hora(minutos[indice_pico_base], x[0])
    pico_ajustado = formato_hora(minutos[indice_pico_ajustado], x[0])

    # Datos meteorológicos horarios para que la integración sea visible.
    if modo_pronostico == "api":
        perturbacion_horaria = perturbacion_meteo
        meteorologia_datos = {
            "horas": horas_meteo.tolist(),
            "temperatura_exterior": temp_exterior.tolist(),
            "radiacion_solar": rad_solar.tolist(),
            "perturbacion": perturbacion_horaria.tolist()
        }
    else:
        meteorologia_datos = None

    return {
        "control_energia": control_energia,
        "anomalias": anomalias,
        "t": t.tolist(),
        "T": T.tolist(),
        "lineal": lineal.tolist(),
        "dT_h": dT_h.tolist(),
        "Tc": Tc.tolist(),
        "dTc": dTc.tolist(),
        "t_decision": t_decision.tolist(),
        "t_pred": t_futuro.tolist(),
        "Tpred": Tpred.tolist(),
        "nodos_x": x.tolist(),
        "nodos_y": y.tolist(),
        "holdout": {
            "n_entrenamiento": int(len(train)),
            "n_prueba": int(len(test)),
            "x_prueba": x[test].tolist(),
            "rmse_spline": rmse(pred_spline, y[test]),
            "rmse_lineal": rmse(pred_lineal, y[test]),
            "error_max_spline": float(np.max(np.abs(pred_spline - y[test]))),
            "error_max_lineal": float(np.max(np.abs(pred_lineal - y[test]))),
            "detalles": holdout_detalles
        },
        "loo": {
            "tipo": "LOO interior",
            "n_evaluaciones": int(n - 2),
            "rmse_spline": rmse_loo_spline,
            "rmse_lineal": rmse_loo_lineal,
            "detalles": loo_detalles
        },
        "verificacion": {
            "diferencia_maxima_manual_scipy": diferencia_manual_scipy,
            "segunda_derivada_inicio": float(cs(x[0], 2)),
            "segunda_derivada_final": float(cs(x[-1], 2))
        },
        "alertas": alertas_base,
        "alertas_ajustadas": alertas_ajustadas,
        "alertas_reactivas": alertas_ajustadas,
        "alertas_preactivas": alertas_preactivas,
        "umbral_calentamiento": float(umbral_calentamiento),
        "umbral_vel_h": float(umbral_calentamiento * 60.0),
        "umbral_temp": float(temperatura_critica),
        "min_calientes": int(len(minutos_calientes)),
        "minutos_calientes": minutos_calientes,
        "intervalos_calientes": intervalos_calientes,
        "min_calientes_ajustada": int(np.sum(sobre_temperatura_ajustada)),
        "intervalos_ajustados": intervalos_ajustados,
        "pico_base": pico_base,
        "temp_pico_base": float(T[indice_pico_base]),
        "pico_perturbado": pico_ajustado,
        "temp_pico_perturbado": float(Tc[indice_pico_ajustado]),
        "meteo_info": meteo_info,
        "meteorologia": meteorologia_datos,
        "tiempo_anticipacion": float(tiempo_anticipacion),
        "hora_pico": float(hora_pico_perturbacion) if modo_pronostico == "simulado" else None
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analizar", methods=["POST"])
def ruta_analizar():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({"error": "La solicitud no contiene un JSON válido."}), 400

    try:
        resultado = analizar(
            datos["x"], datos["y"],
            float(datos["umbral_calentamiento"]),
            float(datos["temperatura_critica"]),
            float(datos["tiempo_anticipacion"]),
            datos["modo_pronostico"],
            float(datos.get("amplitud", 2)),
            float(datos.get("hora_pico", 14)),
            float(datos.get("latitud", BOGOTA_LAT)),
            float(datos.get("longitud", BOGOTA_LON)),
            datos.get("fecha", ""),
            float(datos.get("potencia_ventilador_kw", 1.0)),
            float(datos.get("umbral_anomalia_c", 2.0))
        )
        return jsonify(resultado)
    except KeyError as exc:
        return jsonify({"error": f"Falta el parámetro requerido: {exc.args[0]}."}), 400
    except (ValueError, TypeError, np.linalg.LinAlgError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Error inesperado durante el análisis")
        return jsonify({"error": "Ocurrió un error inesperado al procesar el análisis."}), 500


if __name__ == "__main__":
    app.run(debug=True)
