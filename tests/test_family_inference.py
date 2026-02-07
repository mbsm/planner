"""Test family inference from material description."""
import pytest
from foundryplan.data.data_repository import DataRepositoryImpl


@pytest.fixture
def repo_impl():
    """Fixture to access static method without DB connection."""
    return DataRepositoryImpl


def test_infer_lifter(repo_impl):
    """Test Lifters family inference."""
    assert repo_impl._infer_family_from_description("LIFTER SAG 35° SHARK") == "Lifters"
    assert repo_impl._infer_family_from_description("high linner plate") == "Lifters"
    assert repo_impl._infer_family_from_description("Shark tooth lifter") == "Lifters"
    

def test_infer_parrillas(repo_impl):
    """Test Parrillas family inference."""
    assert repo_impl._infer_family_from_description("GRATE PLATE") == "Parrillas"
    assert repo_impl._infer_family_from_description("parrilla molino") == "Parrillas"


def test_infer_corazas(repo_impl):
    """Test Corazas family inference."""
    assert repo_impl._infer_family_from_description("PLACA CORAZA") == "Corazas"
    assert repo_impl._infer_family_from_description("shell linner plate") == "Corazas"
    assert repo_impl._infer_family_from_description("coraza lateral") == "Corazas"


def test_infer_otros_pulp_lifter(repo_impl):
    """Test that pulp lifter goes to Otros, not Lifters."""
    assert repo_impl._infer_family_from_description("PULP LIFTER PLATE") == "Otros"


def test_infer_no_match(repo_impl):
    """Test that unknown descriptions return None."""
    assert repo_impl._infer_family_from_description("RANDOM PART") is None
    assert repo_impl._infer_family_from_description("") is None
    assert repo_impl._infer_family_from_description(None) is None


def test_case_insensitive(repo_impl):
    """Test that matching is case-insensitive."""
    assert repo_impl._infer_family_from_description("lifter") == "Lifters"
    assert repo_impl._infer_family_from_description("LIFTER") == "Lifters"
    assert repo_impl._infer_family_from_description("LiFtEr") == "Lifters"
    assert repo_impl._infer_family_from_description("GRATE") == "Parrillas"
    assert repo_impl._infer_family_from_description("grate") == "Parrillas"
