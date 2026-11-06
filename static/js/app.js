const modoDatos = document.getElementById("modoDatos");
const modoPronostico = document.getElementById("modoPronostico");
const botonAnalizar = document.getElementById("btnAnalizar");
const errorBox = document.getElementById("error");

function actualizarBloques() {
  document.getElementById("bloqueManual").hidden = modoDatos.value !== "manual";
  document.getElementById("bloqueCsv").hidden = modoDatos.value !== "csv";
  document.getElementById("bloqueApi").hidden = modoPronostico.value !== "api";
  document.getElementById("bloqueSimulado").hidden = modoPronostico.value !== "simulado";
}
modoDatos.addEventListener("change", actualizarBloques);
modoPronostico.addEventListener("change", actualizarBloques);
actualizarBloques();

// Fecha de mañana calculada en hora local, sin conversión UTC.
const manana = new Date();
manana.setDate(manana.getDate() + 1);
document.getElementById("fecha").value = [
  manana.getFullYear(),
  String(manana.getMonth() + 1).padStart(2, "0"),
  String(manana.getDate()).padStart(2, "0")
].join("-");

function leerTexto(texto) {
  const x = [];
  const y = [];
  const lineas = texto.trim().split(/\r?\n/);

  for (let i = 0; i < lineas.length; i++) {
    if (!lineas[i].trim()) continue;
    const p = lineas[i].split(/[,;]/);
    const hora = Number.parseFloat(p[0]);
    const temp = Number.parseFloat(p[1]);

    if (!Number.isFinite(hora) || !Number.isFinite(temp)) {
      if (i === 0) continue; // La primera fila puede ser el encabezado.
      throw new Error(`Línea ${i + 1} no válida: ${lineas[i]}`);
    }
    x.push(hora);
    y.push(temp);
  }

  if (x.length < 3) throw new Error("Ingresa al menos tres mediciones.");
  return { x, y };
}

botonAnalizar.addEventListener("click", async () => {
  errorBox.hidden = true;
  errorBox.textContent = "";

  try {
    let texto;
    if (modoDatos.value === "manual") {
      texto = document.getElementById("datosManual").value;
    } else {
      const archivo = document.getElementById("archivoCsv").files[0];
      if (!archivo) throw new Error("Selecciona un archivo CSV.");
      texto = await archivo.text();
    }
    const datos = leerTexto(texto);
    await enviar(datos.x, datos.y);
  } catch (error) {
    mostrarError(error.message);
  }
});

function mostrarError(mensaje) {
  errorBox.textContent = mensaje;
  errorBox.hidden = false;
  errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function enviar(x, y) {
  botonAnalizar.disabled = true;
  botonAnalizar.innerHTML = '<span class="spinner"></span><span>Procesando análisis...</span>';

  const payload = {
    x, y,
    umbral_calentamiento: document.getElementById("umbralVel").value,
    temperatura_critica: document.getElementById("umbralTemp").value,
    tiempo_anticipacion: document.getElementById("anticipacion").value,
    modo_pronostico: modoPronostico.value,
    amplitud: document.getElementById("amplitud").value,
    hora_pico: document.getElementById("horaPico").value,
    latitud: document.getElementById("lat").value,
    longitud: document.getElementById("lon").value,
    fecha: document.getElementById("fecha").value,
    potencia_ventilador_kw: document.getElementById("potenciaVentilador").value,
    umbral_anomalia_c: document.getElementById("umbralAnomalia").value
  };

  try {
    const respuesta = await fetch("/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const res = await respuesta.json();
    if (!respuesta.ok || res.error) {
      throw new Error(res.error || `Error del servidor (${respuesta.status}).`);
    }
    mostrar(res);
  } catch (error) {
    mostrarError(`No se pudo completar el análisis: ${error.message}`);
  } finally {
    botonAnalizar.disabled = false;
    botonAnalizar.innerHTML = '<span>Ejecutar análisis</span><span class="button-arrow">↗</span>';
  }
}

let graficas = [];

function puntos(xs, ys) {
  return xs.map((x, i) => ({ x, y: ys[i] }));
}

function linea(t, valor) {
  return [{ x: t[0], y: valor }, { x: t[t.length - 1], y: valor }];
}

function curva(nombre, datos, color, punteada = false, extra = {}) {
  return {
    label: nombre,
    data: datos,
    borderColor: color,
    backgroundColor: color,
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 3,
    borderDash: punteada ? [6, 5] : [],
    tension: 0,
    ...extra
  };
}

function dibujar(idCanvas, datasets, ejeY) {
  const canvas = document.getElementById(idCanvas);
  const chart = new Chart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#aebed2", usePointStyle: true, boxWidth: 7, padding: 16, font: { family: "Montserrat", size: 10 } }
        },
        tooltip: { backgroundColor: "#101d32", titleColor: "#f2f7ff", bodyColor: "#c8d7e9", borderColor: "#314762", borderWidth: 1 }
      },
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: "Hora del día (h)", color: "#8fa4bd", font: { family: "Montserrat", size: 10 } },
          ticks: { color: "#8fa4bd" },
          grid: { color: "rgba(142,166,194,.10)" }
        },
        y: {
          title: { display: true, text: ejeY, color: "#8fa4bd", font: { family: "Montserrat", size: 10 } },
          ticks: { color: "#8fa4bd" },
          grid: { color: "rgba(142,166,194,.10)" }
        }
      }
    }
  });
  graficas.push(chart);
}

function numero(valor, decimales = 2) {
  return Number(valor).toFixed(decimales);
}

function renderKpis(res) {
  const items = [
    { label: "Pico térmico ajustado", value: `${numero(res.temp_pico_perturbado)} °C`, detail: `A las ${res.pico_perturbado}`, accent: "cyan" },
    { label: "Máximo calentamiento base", value: `${numero(Math.max(...res.dT_h))} °C/h`, detail: "Derivada máxima del spline", accent: "blue" },
    { label: "Minutos sobre el límite", value: res.min_calientes, detail: `Umbral: ${numero(res.umbral_temp)} °C`, accent: "orange" },
    { label: "Anticipación configurada", value: `${numero(res.tiempo_anticipacion, 0)} min`, detail: "Horizonte de decisión preactiva", accent: "violet" }
  ];
  document.getElementById("kpiGrid").innerHTML = items.map(item => `
    <article class="kpi-card glass ${item.accent}">
      <span>${item.label}</span><strong>${item.value}</strong><small>${item.detail}</small>
    </article>`).join("");
}

function renderWeather(res) {
  const info = res.meteo_info;
  const source = document.getElementById("weatherSource");
  const target = document.getElementById("weatherInfo");
  const disclaimer = document.getElementById("weatherDisclaimer");

  source.textContent = info.fuente;
  if (info.modo === "api") {
    const cards = [
      ["Ubicación", info.ubicacion],
      ["Fecha consultada", info.fecha],
      ["Temperatura exterior media", `${numero(info.temperatura_exterior_media)} °C`],
      ["Radiación solar máxima", `${numero(info.radiacion_max, 0)} W/m²`],
      ["Perturbación media", `${numero(info.perturbacion_media, 3)} °C eq.`],
      ["Zona horaria", info.zona_horaria]
    ];
    target.innerHTML = cards.map(([label, value]) => `<div class="weather-metric"><small>${label}</small><strong>${value}</strong></div>`).join("");
    disclaimer.textContent = `Fuente: Open-Meteo. Se consultaron temperatura a 2 m y radiación solar de onda corta para ${info.ubicacion}. La perturbación es una aproximación con coeficientes ilustrativos (0.10 y 0.30); no representa directamente la temperatura medida dentro del centro de datos.`;
  } else {
    const cards = [
      ["Fuente", "Simulación senoidal"],
      ["Amplitud", `${numero(info.amplitud)} °C`],
      ["Hora del pico", `${numero(info.hora_pico)} h`],
      ["Ubicación", "No aplica"]
    ];
    target.innerHTML = cards.map(([label, value]) => `<div class="weather-metric"><small>${label}</small><strong>${value}</strong></div>`).join("");
    disclaimer.textContent = "La simulación senoidal representa una perturbación externa hipotética. Selecciona Open-Meteo para incorporar variables meteorológicas pronosticadas para Merida.";
  }
}

function renderValidation(res) {
  const h = res.holdout;
  const l = res.loo;
  document.getElementById("validationSummary").innerHTML = `
    <div class="validation-metric"><small>Hold-out · RMSE spline</small><strong>${numero(h.rmse_spline, 4)} °C</strong><span>${h.n_entrenamiento} entrenamiento / ${h.n_prueba} prueba</span></div>
    <div class="validation-metric"><small>Hold-out · RMSE lineal</small><strong>${numero(h.rmse_lineal, 4)} °C</strong><span>Comparación en los mismos nodos</span></div>
    <div class="validation-metric"><small>LOO interior · RMSE spline</small><strong>${numero(l.rmse_spline, 4)} °C</strong><span>${l.n_evaluaciones} nodos interiores evaluados</span></div>
    <div class="validation-metric"><small>LOO interior · RMSE lineal</small><strong>${numero(l.rmse_lineal, 4)} °C</strong><span>Comparación en los mismos nodos</span></div>`;

  const rows = [
    ["Hold-out", ...h.detalles],
    ["LOO interior", ...l.detalles]
  ];
  let html = `<thead><tr><th>Validación</th><th>Hora</th><th>Real (°C)</th><th>Spline (°C)</th><th>Lineal (°C)</th><th>Error spline</th><th>Error lineal</th></tr></thead><tbody>`;
  for (const [tipo, ...detalles] of rows) {
    for (const d of detalles) {
      html += `<tr><td>${tipo}</td><td>${numero(d.hora, 2)} h</td><td>${numero(d.real, 3)}</td><td>${numero(d.spline, 3)}</td><td>${numero(d.lineal, 3)}</td><td>${numero(d.error_spline, 3)}</td><td>${numero(d.error_lineal, 3)}</td></tr>`;
    }
  }
  document.getElementById("validationTable").innerHTML = html + "</tbody>";

  const v = res.verificacion;
  document.getElementById("verificationInfo").innerHTML = `
    <span><b>Verificación manual vs. SciPy:</b> diferencia máxima ${Number(v.diferencia_maxima_manual_scipy).toExponential(2)}</span>
    <span><b>Condiciones naturales:</b> S''(inicio) = ${Number(v.segunda_derivada_inicio).toExponential(2)} · S''(final) = ${Number(v.segunda_derivada_final).toExponential(2)}</span>`;
}

function renderEvents(res) {
  const base = res.alertas || [];
  const ajustadas = res.alertas_ajustadas || [];
  const preactivas = res.alertas_preactivas || [];

  document.getElementById("heatingAlerts").innerHTML = `
    <div class="event-line"><span class="event-label">Umbral</span><strong>${numero(res.umbral_calentamiento, 3)} °C/min (${numero(res.umbral_vel_h, 2)} °C/h)</strong></div>
    <div class="event-line"><span class="event-label">Spline base</span><strong>${base.length ? base.join(", ") : "Sin eventos"}</strong></div>
    <div class="event-line"><span class="event-label">Modelo ajustado</span><strong>${ajustadas.length ? ajustadas.join(", ") : "Sin eventos"}</strong></div>
    <p class="event-explanation">La hora corresponde al primer minuto de cada episodio en el que la derivada supera el umbral.</p>`;

  const intervals = res.intervalos_calientes || [];
  document.getElementById("hotIntervals").innerHTML = `
    <div class="event-total"><strong>${res.min_calientes}</strong><span>muestras de un minuto sobre ${numero(res.umbral_temp)} °C</span></div>
    ${intervals.length ? intervals.map(i => `<div class="interval-row"><strong>${i.inicio} – ${i.fin}</strong><span>${i.cantidad_minutos} min</span></div>`).join("") : '<p class="empty-state">No se superó el límite térmico.</p>'}
    <div class="event-line subtle-line"><span class="event-label">Modelo ajustado</span><strong>${res.min_calientes_ajustada} min sobre el límite</strong></div>`;

  document.getElementById("hotMinutes").innerHTML = (res.minutos_calientes || []).map(m => `<span>${m}</span>`).join("") || '<span class="empty-state">Sin registros</span>';

  document.getElementById("preactiveAlerts").innerHTML = `
    <div class="event-line"><span class="event-label">Anticipación</span><strong>${numero(res.tiempo_anticipacion, 0)} minutos</strong></div>
    ${preactivas.length ? preactivas.map(h => `<div class="alert-chip">Actuar desde ${h}</div>`).join("") : '<p class="empty-state">No se detectó un episodio futuro por encima del umbral de calentamiento en el horizonte válido.</p>'}
    <p class="event-explanation">La alerta se emite en el instante de decisión; la derivada evaluada corresponde al instante posterior t + τ.</p>`;
}

function renderEnergy(res) {
  const e = res.control_energia;
  const ahorro = e.ahorro_porcentaje === null ? "No calculable" : `${numero(e.ahorro_porcentaje, 2)} %`;
  document.getElementById("energySummary").innerHTML = [
    ["Potencia nominal", `${numero(e.potencia_nominal_kw, 2)} kW`],
    ["Todo/Nada", `${numero(e.energia_todonada_kwh, 3)} kWh`],
    ["Velocidad variable", `${numero(e.energia_continua_kwh, 3)} kWh`],
    ["Ahorro estimado", ahorro]
  ].map(([titulo, valor]) => `<div class="validation-metric"><small>${titulo}</small><strong>${valor}</strong></div>`).join("");
  document.getElementById("energyNote").textContent = `${e.modelo} El Todo/Nada enciende a ${numero(e.encendido_c)} °C y apaga a ${numero(e.apagado_c)} °C (histéresis). El control variable combina temperatura (75 %) y derivada positiva (25 %).`;
  dibujar("grafica4", [
    curva("Todo/Nada · potencia", puntos(res.t, e.potencia_todonada_kw), "#e9a85b"),
    curva("Variable · potencia", puntos(res.t, e.potencia_continua_kw), "#55d6b0")
  ], "Potencia eléctrica estimada (kW)");
}

function renderAnomalies(res) {
  const a = res.anomalias;
  document.getElementById("anomalySummary").innerHTML = [
    ["Mediciones evaluadas", a.evaluaciones],
    ["Anomalías detectadas", a.cantidad],
    ["Tolerancia absoluta", `${numero(a.umbral_c)} °C`],
    ["Historial usado", "4 mediciones previas"]
  ].map(([titulo, valor]) => `<div class="validation-metric"><small>${titulo}</small><strong>${valor}</strong></div>`).join("");
  document.getElementById("anomalyTable").innerHTML = `<thead><tr><th>Hora</th><th>Medición (°C)</th><th>Pronóstico (°C)</th><th>Residuo (°C)</th><th>Estado</th></tr></thead><tbody>` +
    a.registros.map(r => `<tr><td>${r.hora_texto}</td><td>${numero(r.medida, 2)}</td><td>${numero(r.esperada, 2)}</td><td>${numero(r.residuo, 2)}</td><td class="${r.anomalia ? "anomaly-alert" : ""}">${r.anomalia ? `Anomalía · ${r.tipo}` : "Dentro del umbral"}</td></tr>`).join("") + "</tbody>";
  document.getElementById("anomalyNote").textContent = `Un residuo alto puede deberse a una variación real, ruido o extrapolación imprecisa; no identifica automáticamente la causa.`;
}

function mostrar(res) {
  document.getElementById("resultados").hidden = false;
  graficas.forEach(g => g.destroy());
  graficas = [];

  renderWeather(res);
  renderKpis(res);
  renderValidation(res);
  renderEvents(res);
  renderAnomalies(res);
  renderEnergy(res);

  dibujar("grafica1", [
    curva("Spline cúbico natural", puntos(res.t, res.T), "#55c7f2"),
    curva("Interpolación lineal", puntos(res.t, res.lineal), "#e9a85b", true),
    { label: "Mediciones", data: puntos(res.nodos_x, res.nodos_y), showLine: false, pointRadius: 4, pointBackgroundColor: "#f3f7fc", pointBorderColor: "#55c7f2" },
    curva("Temperatura crítica", linea(res.t, res.umbral_temp), "#ed747c", true)
  ], "Temperatura (°C)");

  dibujar("grafica2", [
    curva("Temperatura base", puntos(res.t, res.T), "#55c7f2"),
    curva("Temperatura ajustada", puntos(res.t, res.Tc), "#55d6b0"),
    curva("Pronóstico para t + τ", puntos(res.t_pred, res.Tpred), "#b49af7", true),
    curva("Temperatura crítica", linea(res.t, res.umbral_temp), "#ed747c", true)
  ], "Temperatura (°C)");

  dibujar("grafica3", [
    curva("Derivada base", puntos(res.t, res.dT_h), "#55c7f2"),
    curva("Derivada ajustada", puntos(res.t, res.dTc), "#55d6b0"),
    curva("Umbral de calentamiento", linea(res.t, res.umbral_vel_h), "#ed747c", true)
  ], "Velocidad de cambio (°C/h)");

  document.getElementById("resultados").scrollIntoView({ behavior: "smooth", block: "start" });
}
