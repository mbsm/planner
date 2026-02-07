"""Tests for material code extraction functions."""

from foundryplan.data.material_codes import (
    extract_part_code,
    extract_alloy_code,
    get_material_type,
    is_finished_product,
)


def test_extract_part_code_pieza():
    """Test part code extraction from Pieza (40XX00YYYYY)."""
    assert extract_part_code('40330021624') == '21624'
    assert extract_part_code('40450012345') == '12345'
    assert extract_part_code('40210099999') == '99999'


def test_extract_part_code_molde():
    """Test part code extraction from Molde (4310YYYYY01)."""
    assert extract_part_code('43102162401') == '21624'
    assert extract_part_code('43101234501') == '12345'


def test_extract_part_code_fundido():
    """Test part code extraction from Fundido (435XX0YYYYY)."""
    assert extract_part_code('43533021624') == '21624'
    assert extract_part_code('43545012345') == '12345'


def test_extract_part_code_trat_term():
    """Test part code extraction from Trat Term (436XX0YYYYY)."""
    assert extract_part_code('43633021624') == '21624'
    assert extract_part_code('43645012345') == '12345'


def test_extract_part_code_invalid():
    """Test part code extraction with invalid inputs."""
    assert extract_part_code('12345678901') is None  # Wrong prefix
    assert extract_part_code('403300216')   is None  # Too short
    assert extract_part_code('4033002162400') is None  # Too long
    assert extract_part_code('40330121624') is None  # Wrong pattern (no 00)
    assert extract_part_code('')            is None  # Empty
    assert extract_part_code('   ')        is None  # Whitespace


def test_extract_alloy_code_pieza():
    """Test alloy extraction from Pieza."""
    assert extract_alloy_code('40330021624') == '33'
    assert extract_alloy_code('40450021624') == '45'
    assert extract_alloy_code('40210021624') == '21'


def test_extract_alloy_code_fundido():
    """Test alloy extraction from Fundido."""
    assert extract_alloy_code('43533021624') == '33'
    assert extract_alloy_code('43545021624') == '45'


def test_extract_alloy_code_trat_term():
    """Test alloy extraction from Trat Term."""
    assert extract_alloy_code('43633021624') == '33'
    assert extract_alloy_code('43645021624') == '45'


def test_extract_alloy_code_molde():
    """Test alloy extraction from Molde (should return None)."""
    assert extract_alloy_code('43102162401') is None


def test_extract_alloy_code_invalid():
    """Test alloy extraction with invalid inputs."""
    assert extract_alloy_code('12345678901') is None
    assert extract_alloy_code('') is None


def test_get_material_type():
    """Test material type identification."""
    assert get_material_type('40330021624') == 'pieza'
    assert get_material_type('43102162401') == 'molde'
    assert get_material_type('43533021624') == 'fundido'
    assert get_material_type('43633021624') == 'trat_term'
    assert get_material_type('12345678901') is None


def test_is_finished_product():
    """Test finished product detection."""
    assert is_finished_product('40330021624') is True
    assert is_finished_product('43102162401') is False
    assert is_finished_product('43533021624') is False
    assert is_finished_product('43633021624') is False


def test_real_world_examples():
    """Test with real material codes from user's example."""
    # Pieza 40330021624
    assert extract_part_code('40330021624') == '21624'
    assert extract_alloy_code('40330021624') == '33'
    assert get_material_type('40330021624') == 'pieza'
    
    # Molde 43102162401
    assert extract_part_code('43102162401') == '21624'
    assert extract_alloy_code('43102162401') is None
    assert get_material_type('43102162401') == 'molde'
    
    # Fundido 43533021624
    assert extract_part_code('43533021624') == '21624'
    assert extract_alloy_code('43533021624') == '33'
    assert get_material_type('43533021624') == 'fundido'
    
    # Trat Term 43633021624
    assert extract_part_code('43633021624') == '21624'
    assert extract_alloy_code('43633021624') == '33'
    assert get_material_type('43633021624') == 'trat_term'
    
    # All should extract same part code
    codes = [
        '40330021624',
        '43102162401',
        '43533021624',
        '43633021624',
    ]
    part_codes = [extract_part_code(c) for c in codes]
    assert all(pc == '21624' for pc in part_codes)


def test_all_configured_alloys():
    """Test extraction with all configured alloy codes."""
    alloy_codes = ['32', '33', '34', '37', '38', '42', '21', '28']
    
    for alloy in alloy_codes:
        # Pieza: 40XX00YYYYY
        material = f'40{alloy}0012345'
        assert extract_alloy_code(material) == alloy
        assert extract_part_code(material) == '12345'
        
        # Fundido: 435XX0YYYYY
        material = f'435{alloy}012345'
        assert extract_alloy_code(material) == alloy
        assert extract_part_code(material) == '12345'
        
        # Trat Term: 436XX0YYYYY
        material = f'436{alloy}012345'
        assert extract_alloy_code(material) == alloy
        assert extract_part_code(material) == '12345'
