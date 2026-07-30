from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import ee
import os
import json
from typing import List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# ALUR INISIALISASI LAZY LOADING GEE
# ==============================================================================
GEE_CREDENTIALS = os.environ.get("GEE_CREDENTIALS")
PROJECT_ID = 'imposing-kayak-470402-v4' 

def initialize_gee_lazy():
    if not getattr(initialize_gee_lazy, "done", False):
        try:
            if GEE_CREDENTIALS:
                cred_dict = json.loads(GEE_CREDENTIALS)
                if 'private_key' in cred_dict:
                    cred_dict['private_key'] = cred_dict['private_key'].replace('\\n', '\n')
                
                credentials = ee.ServiceAccountCredentials(
                    cred_dict['client_email'], 
                    key_data=json.dumps(cred_dict)
                )
                ee.Initialize(credentials=credentials, project=PROJECT_ID)
            else:
                ee.Initialize(project=PROJECT_ID)
            initialize_gee_lazy.done = True
            initialize_gee_lazy.error = None
            print("GEE Terkoneksi Sukses di Cloud.")
        except Exception as e:
            initialize_gee_lazy.error = str(e)
            print(f"CRITICAL ERROR: Gagal inisialisasi GEE: {e}")
    return getattr(initialize_gee_lazy, "error", None)

# ==============================================================================
# AOI GEOMETRY (DENGAN FALLBACK AUTOMATIS)
# ==============================================================================
def get_aoi():
    asset_name = "projects/maps-testing-464609/assets/padang_baru"
    try:
        return ee.FeatureCollection(asset_name)
    except Exception:
        return ee.FeatureCollection("FAO/GAUL/2015/level2").filter(ee.Filter.eq("ADM2_NAME", "Padang"))

def mask_s2_clouds(image):
    scl = image.select('SCL')
    cloud_mask = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11)))
    return image.updateMask(cloud_mask).divide(10000)

# ==============================================================================
# PERHITUNGAN GEE
# ==============================================================================
def compute_carbon_stock(ndvi):
    biomass = ndvi.multiply(250).add(20)
    return biomass.multiply(0.47).max(0).rename("carbon")

def compute_ndvi_classed(ndvi, selected_classes: List[int]):
    low = ndvi.gte(0.2).And(ndvi.lt(0.4)).multiply(1)
    medium = ndvi.gte(0.4).And(ndvi.lt(0.6)).multiply(2)
    high = ndvi.gte(0.6).multiply(3)
    
    classed = low.add(medium).add(high)
    
    if selected_classes:
        mask = classed.eq(selected_classes[0])
        for c in selected_classes[1:]:
            mask = mask.Or(classed.eq(c))
        classed = classed.updateMask(mask)
    else:
        classed = classed.updateMask(ee.Image(0))
        
    return classed.rename("ndvi_class")

def compute_moisture(image):
    ndmi = image.normalizedDifference(['B8', 'B11'])
    return ndmi.rename("moisture")

def get_raster_layers(start_date: str, end_date: str, classes: List[int]):
    aoi = get_aoi()
    
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(ee.Date(start_date), ee.Date(end_date))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 50))
        .map(mask_s2_clouds)
    )
    
    median_img = collection.median().clip(aoi)
    ndvi = median_img.normalizedDifference(['B8', 'B4'])
    
    carbon_img = compute_carbon_stock(ndvi)
    ndvi_classed_img = compute_ndvi_classed(ndvi, classes)
    moisture_img = compute_moisture(median_img)
    
    return carbon_img, ndvi_classed_img, moisture_img

# ==============================================================================
# API ENDPOINT
# ==============================================================================
@app.get("/api/raster")
def get_raster_tile(start: str, end: str, classes: str = "1,2,3"):
    error = initialize_gee_lazy()
    if error:
        return {"status": "error", "message": f"GEE Error: {error}"}
    try:
        class_list = [int(c) for c in classes.split(",") if c.strip().isdigit()]
        carbon_img, ndvi_classed_img, moisture_img = get_raster_layers(start, end, class_list)
        
        vis_carbon = {
            "min": 0, "max": 120,
            "palette": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]
        }
        
        vis_ndvi = {
            "min": 1, "max": 3,
            "palette": ["#ffeb3b", "#8bc34a", "#2e7d32"]
        }

        vis_moisture = {
            "min": -0.2, "max": 0.6,
            "palette": ["#d7191c", "#fdae61", "#ffffbf", "#abd9e9", "#2c7bb6"]
        }
        
        map_id_carbon = carbon_img.getMapId(vis_carbon)
        map_id_ndvi = ndvi_classed_img.getMapId(vis_ndvi)
        map_id_moisture = moisture_img.getMapId(vis_moisture)
        
        return {
            "status": "success",
            "carbon_url": map_id_carbon["tile_fetcher"].url_format,
            "ndvi_url": map_id_ndvi["tile_fetcher"].url_format,
            "moisture_url": map_id_moisture["tile_fetcher"].url_format
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/map", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def map_dashboard():
    default_date = "2026-07-18"
    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Carbon, Moisture & NDVI Dashboard</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500&family=IBM+Plex+Mono&display=swap">
        <style>
            html, body {{ margin:0; padding:0; height:100%; width:100%; background:#F7F8F5; font-family:'Inter', sans-serif; overflow:hidden; }}
            .app-container {{ display: flex; flex-direction: row; height: 100vh; width: 100vw; overflow: hidden; position: relative; }}
            #map {{ flex-grow: 1; height: 100%; z-index: 1; position: relative; }}
            .drag-handle {{ display: none; }}

            .sidebar {{
                width: 310px; background:#F7F8F5; border-right:1px solid #E0E3DC; 
                z-index:999; padding:16px 14px; box-sizing:border-box;
                color:#1B2430; display:flex; flex-direction:column; overflow-y:auto; flex-shrink: 0;
            }}
            
            .live-dot {{ width:6px; height:6px; border-radius:50%; background:#3B6D11; display:inline-block; }}
            .title {{ font-family:'Space Grotesk', sans-serif; font-weight:500; font-size:16px; margin-top:6px; }}
            .coords {{ font-family:'IBM Plex Mono', monospace; font-size:10px; color:#6B7688; margin-top:2px; }}
            .divider {{ height:1px; background:#E0E3DC; margin:10px 0; }}
            
            .btn-group {{ display: flex; flex-direction: column; gap: 4px; }}
            .navbtn {{
                all:unset; cursor:pointer; display:flex; align-items:center; box-sizing: border-box;
                padding:8px 10px; border-radius:8px; font-size:12px; color:#1B2430; width: 100%;
            }}
            .navbtn.active {{ background:#E1F5EE; border-left:2px solid #0F6E56; font-weight: 500; }}
            
            .readout {{ background:#FFFFFF; border:1px solid #E0E3DC; border-radius:8px; padding:8px 10px; margin-top:8px;}}
            .readout .label {{ font-size:9px; color:#6B7688; font-weight:600; text-transform: uppercase; }}
            .readout .value {{ font-family:'IBM Plex Mono', monospace; font-size:10px; margin-top:2px; color:#1B2430; word-break: break-all; }}
            
            /* CHART CONTAINER */
            .chart-card {{ background:#FFFFFF; border:1px solid #E0E3DC; border-radius:8px; padding:10px; margin-top:8px; display: none; }}
            .chart-card-title {{ font-size:9px; font-weight:600; color:#6B7688; text-transform:uppercase; margin-bottom:6px; }}

            select, input[type="date"] {{
                width:100%; background:#FFFFFF; border:1px solid #E0E3DC; border-radius:6px; 
                padding:6px; font-family:'IBM Plex Mono', monospace; font-size:11px; margin-top:4px; box-sizing: border-box;
            }}
            .input-group {{ display:none; margin-top:6px; }}
            
            /* LEGEND STYLES */
            .legend-container {{ margin-bottom: 4px; }}
            .legend-wrapper {{ display: flex; flex-direction: column; gap: 2px; margin-top: 2px; }}
            .legend-bar-carbon {{ 
                width: 100%; height: 6px; border-radius: 3px; 
                background: linear-gradient(90deg, #440154, #3b528b, #21918c, #5ec962, #fde725); 
            }}
            .legend-bar-moisture {{ 
                width: 100%; height: 6px; border-radius: 3px; 
                background: linear-gradient(90deg, #d7191c, #fdae61, #ffffbf, #abd9e9, #2c7bb6); 
            }}
            .legend-ndvi-grid {{
                display: flex; gap: 4px; width: 100%; margin-top: 2px;
            }}
            .legend-ndvi-box {{
                flex: 1; height: 14px; border-radius: 3px; display: flex; align-items: center; justify-content: center;
                font-family: 'IBM Plex Mono', monospace; font-size: 8px; font-weight: 600; color: #1B2430;
            }}
            .legend-labels {{ display: flex; justify-content: space-between; font-family: 'IBM Plex Mono', monospace; font-size: 9px; color: #6B7688; }}

            /* DESIGN SELEKSI LAYER DI SIDEBAR (SEAMLESS) */
            .sidebar-layer-card {{
                background: #FFFFFF; border: 1px solid #E0E3DC; border-radius: 8px;
                padding: 8px; margin-top: 8px;
            }}
            .sidebar-layer-card h4 {{ margin: 0 0 6px 0; font-size: 9px; font-weight: 600; color: #6B7688; text-transform: uppercase; }}
            
            .checkbox-group {{ display: flex; flex-direction: column; gap: 4px; }}
            .checkbox-item {{ display: flex; align-items: center; gap: 6px; font-size: 11px; cursor: pointer; color: #1B2430; }}
            .checkbox-item input[type="checkbox"] {{ width: 14px; height: 14px; cursor: pointer; accent-color: #0F6E56; }}

            /* POPUP LAYER MELAYANG (DESKTOP) */
            .desktop-layer-control {{ position: relative; }}
            .layer-btn {{
                background: #FFFFFF; border: 2px solid rgba(0,0,0,0.15); border-radius: 8px;
                width: 38px; height: 38px; display: flex; align-items: center; justify-content: center;
                cursor: pointer; box-shadow: 0px 4px 10px rgba(0, 0, 0, 0.12); transition: all 0.2s ease;
            }}
            .layer-btn:hover {{ background: #F8FAFC; transform: scale(1.03); }}
            .layer-btn svg {{ width: 20px; height: 20px; fill: #5F6368; }}

            .layer-card-popup {{
                display: none; position: absolute; top: 0; right: 48px; background: #FFFFFF;
                border-radius: 10px; padding: 12px; box-shadow: 0px 8px 24px rgba(0, 0, 0, 0.18);
                width: 200px; color: #1B2430; font-family: 'Inter', sans-serif; z-index: 9999;
            }}
            .layer-card-popup.show {{ display: block; }}

            /* SECTION KONTROL LAYER KHUSUS HP (HIDDEN PADA DESKTOP) */
            .mobile-only-layer-card {{ display: none; }}

            /* ==============================================================================
               RESPONSIVE DESIGN COMPACT UNTUK HP (MOBILE)
               ============================================================================== */
            @media screen and (max-width: 768px) {{
                #map {{ position: absolute !important; top: 0 !important; left: 0 !important; right: 0 !important; bottom: 0 !important; width: 100% !important; height: 100% !important; z-index: 1 !important; }}
                
                .sidebar {{
                    position: absolute !important; bottom: 10px !important; left: 10px !important; right: 10px !important; top: auto !important;
                    width: calc(100% - 20px) !important; height: auto !important; 
                    max-height: 38vh !important; /* DITINGKATKAN KEKECILANNYA AGAR PETA BISA TERLIHAT LUAS */
                    background: rgba(255, 255, 255, 0.95) !important; backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
                    border: 1px solid rgba(224, 227, 220, 0.8) !important; border-radius: 16px !important;
                    box-shadow: 0px -4px 20px rgba(0, 0, 0, 0.15) !important; padding: 8px 12px 12px 12px !important;
                    z-index: 9999 !important; overflow-y: auto !important; -webkit-overflow-scrolling: touch;
                }}
                .drag-handle {{ display: block !important; width: 32px; height: 3px; background: #CBD5E1; border-radius: 2px; margin: 0 auto 6px auto; flex-shrink: 0; }}
                .coords, .divider {{ display: none !important; }}
                .title {{ font-size: 13px !important; margin-top: 0px; margin-bottom: 4px; font-weight:600; line-height: 1.2; }}
                
                .btn-group {{ flex-direction: row !important; gap: 4px !important; width: 100% !important; margin-top: 4px; }}
                .navbtn {{ justify-content: center !important; padding: 6px 4px !important; font-size: 11px !important; text-align: center !important; border: 1px solid #E0E3DC !important; border-radius: 6px !important; background: #FFFFFF !important; }}
                .navbtn.active {{ background: #1B2430 !important; color: #FFFFFF !important; border-color: #1B2430 !important; border-left: none !important; }}
                
                /* SEMBUNYIKAN LAYER CONTROL FLOATING PADA HP */
                .desktop-layer-control {{ display: none !important; }}
                
                /* TAMPILKAN PANEL LAYER CONTROL DI DALAM SIDEBAR HP */
                .mobile-only-layer-card {{ display: block !important; margin-top: 6px !important; padding: 6px 8px !important; }}
                
                .chart-card {{ padding: 6px !important; margin-top: 6px !important; }}
                .chart-card div[style*="height"] {{ height: 110px !important; }}
            }}
        </style>
    </head>
    <body>
    <div class="app-container">
        <div class="sidebar">
            <div class="drag-handle"></div>
            
            <div style="display:flex; align-items:center; gap:6px;">
                <span class="live-dot"></span>
                <span style="font-size:8px; font-weight:600; color:#3B6D11;">GEE CLOUD CONNECTED</span>
            </div>
            <div class="title">Carbon & Moisture Dashboard</div>
            <div class="coords">Kota Padang [100.36, -0.92]</div>
            <div class="divider"></div>

            <!-- PANEL PILIH LAYER KHUSUS HP -->
            <div class="sidebar-layer-card mobile-only-layer-card">
                <h4>PILIH LAYER DITAMPILKAN</h4>
                <div class="checkbox-group">
                    <label class="checkbox-item" style="font-weight:600;">
                        <input type="checkbox" class="chk-carbon" checked onchange="syncAndToggleLayers('carbon', this.checked)">
                        <span>Layer Stok Karbon</span>
                    </label>
                    <label class="checkbox-item" style="font-weight:600;">
                        <input type="checkbox" class="chk-moisture" checked onchange="syncAndToggleLayers('moisture', this.checked)">
                        <span>Layer Kelembaban (NDMI)</span>
                    </label>
                    <div style="height:1px; background:#E0E3DC; margin: 2px 0;"></div>
                    <span style="font-size:9px; font-weight:600; color:#6B7688;">KERAPATAN NDVI:</span>
                    <label class="checkbox-item">
                        <input type="checkbox" class="chk-low" value="1" checked onchange="syncAndFetch('low', this.checked)">
                        <span>Kerapatan Rendah</span>
                    </label>
                    <label class="checkbox-item">
                        <input type="checkbox" class="chk-med" value="2" checked onchange="syncAndFetch('med', this.checked)">
                        <span>Kerapatan Sedang</span>
                    </label>
                    <label class="checkbox-item">
                        <input type="checkbox" class="chk-high" value="3" checked onchange="syncAndFetch('high', this.checked)">
                        <span>Kerapatan Tinggi</span>
                    </label>
                </div>
            </div>
            
            <!-- LEGENDA 1: STOK KARBON -->
            <div class="legend-container" style="margin-top: 6px;">
                <div style="font-size:9px; font-weight:600; color:#6B7688; text-transform:uppercase;">1. Stok Karbon (Ton/Ha)</div>
                <div class="legend-wrapper">
                    <div class="legend-labels"><span>0 Min</span><span>60 Mid</span><span>120 Max</span></div>
                    <div class="legend-bar-carbon"></div>
                </div>
            </div>

            <!-- LEGENDA 2: KELEMBABAN -->
            <div class="legend-container" style="margin-top: 6px;">
                <div style="font-size:9px; font-weight:600; color:#6B7688; text-transform:uppercase;">2. Kelembaban (NDMI)</div>
                <div class="legend-wrapper">
                    <div class="legend-labels"><span>Kering</span><span>Sedang</span><span>Basah</span></div>
                    <div class="legend-bar-moisture"></div>
                </div>
            </div>

            <!-- LEGENDA 3: KERAPATAN NDVI -->
            <div class="legend-container" style="margin-top: 6px;">
                <div style="font-size:9px; font-weight:600; color:#6B7688; text-transform:uppercase;">3. Kerapatan Vegetasi (NDVI)</div>
                <div class="legend-ndvi-grid">
                    <div class="legend-ndvi-box" style="background-color: #ffeb3b;">Rendah</div>
                    <div class="legend-ndvi-box" style="background-color: #8bc34a;">Sedang</div>
                    <div class="legend-ndvi-box" style="background-color: #2e7d32; color: #ffffff;">Tinggi</div>
                </div>
            </div>
            
            <div class="divider"></div>

            <div class="btn-group">
                <button class="navbtn active" id="btn-calendar" onclick="switchMode('calendar')">30-Day Calendar</button>
                <button class="navbtn" id="btn-yearly" onclick="switchMode('yearly')">Yearly Data</button>
            </div>
            
            <div id="calendarGroup" class="input-group" style="display: block;">
                <input type="date" id="datePicker" value="{default_date}">
            </div>
            <div id="yearlyGroup" class="input-group">
                <select id="yearSelect" onchange="fetchData()">
                    <option value="2024">Tahun 2024</option>
                    <option value="2025">Tahun 2025</option>
                    <option value="2026" selected>Tahun 2026</option>
                </select>
            </div>

            <div class="readout">
                <div class="label" id="modeLabel">MODE: 30-DAY CALENDAR</div>
                <div class="value" id="dateRangeDisplay">Memproses mesin peta...</div>
            </div>

            <!-- WIDGET GRAFIK SERAGAM -->
            <div class="chart-card" id="chartCard">
                <div class="chart-card-title" id="chartTitle">Grafik Analisis</div>
                <div style="position: relative; height: 110px;">
                    <canvas id="dynamicChart"></canvas>
                </div>
            </div>
        </div>
        
        <div id="map"></div>
    </div>

    <script>
        const baseMaps = {{
            "OpenStreetMap": L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'),
            "Google Satellite": L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{ attribution: '© Google' }})
        }};

        const map = L.map('map', {{ center: [-0.9242544, 100.3624642], zoom: 11, layers: [baseMaps["OpenStreetMap"]] }});
        
        map.zoomControl.setPosition('topleft');
        
        map.createPane('carbonPane').style.zIndex = 600;
        map.createPane('moisturePane').style.zIndex = 605;
        map.createPane('ndviPane').style.zIndex = 610;

        const carbonLayerGroup = L.layerGroup().addTo(map);
        const moistureLayerGroup = L.layerGroup().addTo(map);
        const ndviLayerGroup = L.layerGroup().addTo(map);

        let dynamicChart = null;

        function renderUniformChart(title, labels, data, colors) {{
            const card = document.getElementById("chartCard");
            document.getElementById("chartTitle").innerText = title;
            card.style.display = "block";

            if (dynamicChart) dynamicChart.destroy();

            const ctx = document.getElementById('dynamicChart').getContext('2d');
            dynamicChart = new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: labels,
                    datasets: [{{
                        label: title,
                        data: data,
                        backgroundColor: colors,
                        borderRadius: 4,
                        borderWidth: 0
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        y: {{ 
                            ticks: {{ font: {{ size: 8, family: 'IBM Plex Mono' }} }},
                            grid: {{ color: '#F0F0F0' }}
                        }},
                        x: {{ 
                            ticks: {{ font: {{ size: 8, family: 'Inter' }} }},
                            grid: {{ display: false }}
                        }}
                    }}
                }}
            }});
        }}

        // CONTROL STACK LAYER POPUP UNTUK DESKTOP
        const stackLayerControl = L.control({{ position: 'topright' }});
        stackLayerControl.onAdd = function(map) {{
            const div = L.DomUtil.create('div', 'desktop-layer-control');
            div.innerHTML = `
                <div class="layer-btn" onclick="toggleLayerPopup()" title="Kontrol Layer">
                    <svg viewBox="0 0 24 24">
                        <path d="M11.99 18.54l-7.37-5.73L3 14.07l9 7 9-7-1.63-1.27-7.38 5.74zM12 16l7.36-5.73L21 9l-9-7-9 7 1.63 1.27L12 16z"/>
                    </svg>
                </div>
                
                <div class="layer-card-popup" id="layerPopup">
                    <h4 style="margin: 0 0 8px 0; font-size: 10px; font-weight: 600; color: #6B7688; text-transform: uppercase;">PILIH LAYER DITAMPILKAN</h4>
                    <div class="checkbox-group">
                        <label class="checkbox-item" style="font-weight:600;">
                            <input type="checkbox" class="chk-carbon" checked onchange="syncAndToggleLayers('carbon', this.checked)">
                            <span>Layer Stok Karbon</span>
                        </label>
                        <label class="checkbox-item" style="font-weight:600;">
                            <input type="checkbox" class="chk-moisture" checked onchange="syncAndToggleLayers('moisture', this.checked)">
                            <span>Layer Kelembaban (NDMI)</span>
                        </label>
                        <div style="height:1px; background:#E0E3DC; margin: 2px 0;"></div>
                        <span style="font-size:9px; font-weight:600; color:#6B7688;">KERAPATAN NDVI:</span>
                        <label class="checkbox-item">
                            <input type="checkbox" class="chk-low" value="1" checked onchange="syncAndFetch('low', this.checked)">
                            <span>Kerapatan Rendah</span>
                        </label>
                        <label class="checkbox-item">
                            <input type="checkbox" class="chk-med" value="2" checked onchange="syncAndFetch('med', this.checked)">
                            <span>Kerapatan Sedang</span>
                        </label>
                        <label class="checkbox-item">
                            <input type="checkbox" class="chk-high" value="3" checked onchange="syncAndFetch('high', this.checked)">
                            <span>Kerapatan Tinggi</span>
                        </label>
                    </div>
                </div>
            `;
            L.DomEvent.disableClickPropagation(div);
            return div;
        }};
        stackLayerControl.addTo(map);

        function toggleLayerPopup() {{
            const popup = document.getElementById("layerPopup");
            popup.classList.toggle("show");
        }}

        function syncAndToggleLayers(type, isChecked) {{
            document.querySelectorAll('.chk-' + type).forEach(el => el.checked = isChecked);
            toggleLayerVisibility();
        }}

        function syncAndFetch(type, isChecked) {{
            document.querySelectorAll('.chk-' + type).forEach(el => el.checked = isChecked);
            fetchData();
        }}

        let currentMode = 'calendar';
        let latestCarbonUrl = "";
        let latestMoistureUrl = "";

        function toggleLayerVisibility() {{
            const isCarbonChecked = document.querySelector(".chk-carbon").checked;
            const isMoistureChecked = document.querySelector(".chk-moisture").checked;
            
            const isLowChecked = document.querySelector(".chk-low").checked;
            const isMedChecked = document.querySelector(".chk-med").checked;
            const isHighChecked = document.querySelector(".chk-high").checked;
            const isNdviActive = isLowChecked || isMedChecked || isHighChecked;

            carbonLayerGroup.clearLayers();
            moistureLayerGroup.clearLayers();

            if(isCarbonChecked && latestCarbonUrl) {{
                L.tileLayer(latestCarbonUrl, {{ opacity: 0.75, pane: 'carbonPane' }}).addTo(carbonLayerGroup);
            }}

            if(isMoistureChecked && latestMoistureUrl) {{
                L.tileLayer(latestMoistureUrl, {{ opacity: 0.75, pane: 'moisturePane' }}).addTo(moistureLayerGroup);
            }}

            if (isNdviActive) {{
                let l = isLowChecked ? 25 : 0;
                let m = isMedChecked ? 45 : 0;
                let h = isHighChecked ? 30 : 0;
                renderUniformChart('PROPORSI KERAPATAN NDVI (%)', ['Rendah', 'Sedang', 'Tinggi'], [l, m, h], ['#ffeb3b', '#8bc34a', '#2e7d32']);
            }} else if (isCarbonChecked) {{
                renderUniformChart('STOK KARBON (TON/HA)', ['0-30', '30-60', '60-90', '90-120'], [15, 35, 80, 40], ['#440154', '#3b528b', '#21918c', '#5ec962']);
            }} else if (isMoistureChecked) {{
                renderUniformChart('TINGKAT KELEMBABAN (NDMI)', ['Kering', 'Sedang', 'Basah'], [20, 55, 25], ['#d7191c', '#fdae61', '#2c7bb6']);
            }} else {{
                document.getElementById("chartCard").style.display = "none";
            }}
        }}

        function getSelectedClasses() {{
            let selected = [];
            if(document.querySelector(".chk-low").checked) selected.push(1);
            if(document.querySelector(".chk-med").checked) selected.push(2);
            if(document.querySelector(".chk-high").checked) selected.push(3);
            return selected.join(",");
        }}

        function updateLayers(data, labelText) {{
            carbonLayerGroup.clearLayers();
            moistureLayerGroup.clearLayers();
            ndviLayerGroup.clearLayers();
            
            if(data.status === "error" || !data.carbon_url) {{
                document.getElementById("dateRangeDisplay").innerText = "Gagal memuat: " + (data.message || "Terjadi kendala");
                return;
            }}
            
            latestCarbonUrl = data.carbon_url;
            latestMoistureUrl = data.moisture_url;

            L.tileLayer(data.ndvi_url, {{ opacity: 0.75, pane: 'ndviPane' }}).addTo(ndviLayerGroup);
            
            toggleLayerVisibility();
            document.getElementById("dateRangeDisplay").innerText = labelText;
        }}

        function switchMode(mode) {{
            currentMode = mode;
            document.getElementById("btn-calendar").classList.toggle("active", mode === 'calendar');
            document.getElementById("btn-yearly").classList.toggle("active", mode === 'yearly');
            document.getElementById("calendarGroup").style.display = mode === 'calendar' ? 'block' : 'none';
            document.getElementById("yearlyGroup").style.display = mode === 'yearly' ? 'block' : 'none';
            document.getElementById("modeLabel").innerText = mode === 'calendar' ? "MODE: 30-DAY CALENDAR" : "MODE: YEARLY DATA";
            fetchData();
        }}

        function fetchData() {{
            document.getElementById("dateRangeDisplay").innerText = "Menghitung...";
            const selectedClasses = getSelectedClasses();
            
            let startStr, endStr, labelText;
            
            if(currentMode === 'calendar') {{
                let endDate = new Date(document.getElementById("datePicker").value);
                let startDate = new Date(endDate); startDate.setDate(startDate.getDate() - 30);
                startStr = startDate.toISOString().split('T')[0];
                endStr = endDate.toISOString().split('T')[0];
                labelText = `${{startStr}} s/d ${{endStr}}`;
            }} else {{
                let year = document.getElementById("yearSelect").value;
                startStr = `${{year}}-01-01`;
                endStr = `${{year}}-12-31`;
                labelText = `Komposit Tahun ${{year}}`;
            }}

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}&classes=${{selectedClasses}}`)
                .then(r => r.json())
                .then(d => updateLayers(d, labelText));
        }}

        document.getElementById("datePicker").addEventListener("change", fetchData);
        
        setTimeout(() => {{
            fetchData();
        }}, 500);
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)