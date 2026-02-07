"""Material code parsing utilities.

SAP material codes follow these patterns:
- Pieza:      40XX00YYYYY  (XX = alloy code, YYYYY = part code)
- Molde:      4310YYYYY01  (YYYYY = part code, no alloy)
- Fundido:    435XX0YYYYY  (XX = alloy code, YYYYY = part code)
- Trat Term:  436XX0YYYYY  (XX = alloy code, YYYYY = part code)
"""

from __future__ import annotations


def extract_part_code(material: str) -> str | None:
    """Extract 5-digit part code (YYYYY) from SAP material code.
    
    Args:
        material: 11-digit SAP material code
        
    Returns:
        5-digit part code or None if pattern not recognized
        
    Examples:
        >>> extract_part_code('40330021624')
        '21624'
        >>> extract_part_code('43102162401')
        '21624'
        >>> extract_part_code('43533021624')
        '21624'
        >>> extract_part_code('43633021624')
        '21624'
        >>> extract_part_code('12345678901')
        None
    """
    material = str(material).strip()
    if len(material) != 11:
        return None
    
    # Pieza: 40XX00YYYYY → positions [6:11]
    if material.startswith('40') and material[4:6] == '00':
        return material[6:11]
    
    # Molde: 4310YYYYY01 → positions [4:9]
    if material.startswith('4310') and material[9:11] == '01':
        return material[4:9]
    
    # Fundido: 435XX0YYYYY → positions [6:11]
    if material.startswith('435') and material[5] == '0':
        return material[6:11]
    
    # Trat.Term: 436XX0YYYYY → positions [6:11]
    if material.startswith('436') and material[5] == '0':
        return material[6:11]
    
    return None


def extract_alloy_code(material: str) -> str | None:
    """Extract 2-digit alloy code (XX) from SAP material code.
    
    Args:
        material: 11-digit SAP material code
        
    Returns:
        2-digit alloy code or None if not applicable (Molde type has no alloy)
        
    Examples:
        >>> extract_alloy_code('40330021624')
        '33'
        >>> extract_alloy_code('43533021624')
        '33'
        >>> extract_alloy_code('43633021624')
        '33'
        >>> extract_alloy_code('43102162401')
        None
    """
    material = str(material).strip()
    if len(material) != 11:
        return None
    
    # Pieza: 40XX00YYYYY → positions [2:4]
    if material.startswith('40') and material[4:6] == '00':
        return material[2:4]
    
    # Fundido: 435XX0YYYYY → positions [3:5]
    if material.startswith('435') and material[5] == '0':
        return material[3:5]
    
    # Trat.Term: 436XX0YYYYY → positions [3:5]
    if material.startswith('436') and material[5] == '0':
        return material[3:5]
    
    # Molde has no alloy code
    return None


def get_material_type(material: str) -> str | None:
    """Identify material type from SAP code.
    
    Args:
        material: 11-digit SAP material code
        
    Returns:
        'pieza', 'molde', 'fundido', 'trat_term', or None if unrecognized
        
    Examples:
        >>> get_material_type('40330021624')
        'pieza'
        >>> get_material_type('43102162401')
        'molde'
        >>> get_material_type('43533021624')
        'fundido'
        >>> get_material_type('43633021624')
        'trat_term'
    """
    material = str(material).strip()
    if len(material) != 11:
        return None
    
    if material.startswith('40') and material[4:6] == '00':
        return 'pieza'
    
    if material.startswith('4310') and material[9:11] == '01':
        return 'molde'
    
    if material.startswith('435') and material[5] == '0':
        return 'fundido'
    
    if material.startswith('436') and material[5] == '0':
        return 'trat_term'
    
    return None


def is_finished_product(material: str) -> bool:
    """Check if material code represents finished product (Pieza type).
    
    Args:
        material: 11-digit SAP material code
        
    Returns:
        True if material is Pieza (40XX00YYYYY), False otherwise
        
    Examples:
        >>> is_finished_product('40330021624')
        True
        >>> is_finished_product('43102162401')
        False
    """
    return get_material_type(material) == 'pieza'


def extract_part_code_sql(column_name: str) -> str:
    """Generate SQL CASE expression to extract part_code from a material column.
    
    Use this helper to generate consistent SQL for extracting part_code in queries.
    
    Args:
        column_name: Name of the SQL column containing material code
        
    Returns:
        SQL CASE expression that extracts 5-digit part code
        
    Example:
        >>> extract_part_code_sql('m.material')
        "CASE WHEN substr(m.material, 1, 2) = '40' AND substr(m.material, 5, 2) = '00' THEN substr(m.material, 7, 5) ..."
        
    Usage in queries:
        f"LEFT JOIN core_material_master p ON p.part_code = {extract_part_code_sql('v.cod_material')}"
    """
    return f"""CASE
        WHEN substr({column_name}, 1, 2) = '40' AND substr({column_name}, 5, 2) = '00' THEN substr({column_name}, 7, 5)
        WHEN substr({column_name}, 1, 4) = '4310' AND substr({column_name}, 10, 2) = '01' THEN substr({column_name}, 5, 5)
        WHEN substr({column_name}, 1, 3) = '435' AND substr({column_name}, 6, 1) = '0' THEN substr({column_name}, 7, 5)
        WHEN substr({column_name}, 1, 3) = '436' AND substr({column_name}, 6, 1) = '0' THEN substr({column_name}, 7, 5)
    END"""
