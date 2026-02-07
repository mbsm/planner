"""
Real data fixtures extracted from sample_data/*.XLSX files.

These fixtures use actual SAP snapshots for more realistic testing.
All data is from February 2026 production snapshots.
"""

# FIXTURE 1: Pedido 1010029944/40
# Single lote, simple case for basic MB52+Vision integration testing

FIXTURE_MB52_SINGLE_REAL = [
    {
        'material': '40420099239',
        'centro': '4000',
        'almacen': '4047',
        'lote': '0001PI0077',
        'libre_utilizacion': 1,
        'en_control_calidad': 0,
        'documento_comercial': '1010029944',
        'posicion_sd': '40'
    },
]

FIXTURE_VISION_SINGLE_REAL = {
    'pedido': '1010029944',
    'posicion': '40',
    'cod_material': '40420099239',
    'fecha_de_pedido': '2026-02-02',
    'solicitado': 1,
    'descripcion_material': 'GRATE BAR TYPE "X" CM2 19MM'
}


# FIXTURE 2: Pedido 1010039081/30
# Multiple lotes (3), good for job aggregation testing

FIXTURE_MB52_MULTI_REAL = [
    {
        'material': '40380020305',
        'centro': '4000',
        'almacen': '4046',
        'lote': '0001',
        'libre_utilizacion': 1,
        'en_control_calidad': 0,
        'documento_comercial': '1010039081',
        'posicion_sd': '30'
    },
    {
        'material': '40380020305',
        'centro': '4000',
        'almacen': '4046',
        'lote': '0002',
        'libre_utilizacion': 1,
        'en_control_calidad': 0,
        'documento_comercial': '1010039081',
        'posicion_sd': '30'
    },
    {
        'material': '40380020305',
        'centro': '4000',
        'almacen': '4046',
        'lote': '0003',
        'libre_utilizacion': 1,
        'en_control_calidad': 0,
        'documento_comercial': '1010039081',
        'posicion_sd': '30'
    },
]

FIXTURE_VISION_MULTI_REAL = {
    'pedido': '1010039081',
    'posicion': '30',
    'cod_material': '40380020305',
    'fecha_de_pedido': '2026-02-01',
    'solicitado': 6,
    'descripcion_material': 'LIFTER BAR TYPE "D" SLOTS 1" CM3'
}


# FIXTURE 3: SAP Normalization test cases
# Tests for handling float conversions in SAP keys

FIXTURE_SAP_NORMALIZATION_CASES = [
    # Pedido stored as float in Excel (1010045232.0) should match Vision (1010045232)
    {
        'mb52': {
            'documento_comercial': 1010045232.0,  # float from Excel
            'posicion_sd': 10.0,  # float from Excel
        },
        'vision': {
            'pedido': 1010045232,  # int in Vision
            'posicion': 10,  # int in Vision
        },
        'expected_key': '1010045232/10'
    },
    # String with leading zeros
    {
        'mb52': {
            'documento_comercial': '1010012831',
            'posicion_sd': '0020',  # Leading zero
        },
        'vision': {
            'pedido': 1010012831,
            'posicion': 20,  # Without zero
        },
        'expected_key': '1010012831/20'  # Should match
    },
]


# Real Desmoldeo (demolding) fixtures
# Based on desmoldeo.XLSX

FIXTURE_DESMOLDEO_WIP = [
    {
        'pieza': '43102162401',  # Molde code
        'tipo_pieza': 'MOLDE PIEZA 40330021624',  # Description with part_code
        'lote': '0000482266',
        'caja': '105" X 105" X 25"',
        'cancha': 'TCF-L1200',
        'fecha_desmoldeo': None,  # NULL = still in process (WIP)
        'hora_desm': '00:00:00',
        'fecha_fundida': '2026-02-01',
        'hora_fundida': '08:30:00',
        'hs_enfria': 48,  # Cooling hours
        'cant_moldes': 1.0,  # Quantity (can be fractional: 0.25, 0.5, 1.0)
    },
]

FIXTURE_DESMOLDEO_COMPLETED = [
    {
        'pieza': '43102190501',
        'tipo_pieza': 'MOLDE PIEZA 40330021905',
        'lote': '0000482300',
        'caja': '143" X 143" X 40"',
        'cancha': 'TCF-L1400',
        'fecha_desmoldeo': '2026-02-04',  # NOT NULL = completed
        'hora_desm': '14:00:00',
        'fecha_fundida': '2026-02-01',
        'hora_fundida': '16:00:00',
        'hs_enfria': 72,
        'cant_moldes': 0.5,  # Half flask (2 molds per flask)
    },
]
