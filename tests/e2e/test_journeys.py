"""Physician, patient and pharmacist journeys in a real browser.

Every step is something a person does on the screen; if a UI change breaks
the flow, this fails before anyone else notices.  AI is disabled on the test
server, so the assertions cover the local, deterministic parts only.
"""
import re

import pytest
from playwright.sync_api import expect


@pytest.mark.e2e
def test_physician_dashboard_copilot_and_smart_diagnosis(login, server_url):
    page = login('physician')
    expect(page).to_have_url(re.compile(r'/doctor/dashboard'))
    expect(page.locator('.ai-strip-title').first).to_contain_text('AI models')
    # one Copilot entry point (sidebar) opens the panel; Esc closes it
    page.click('.sb-ai-btn')
    panel = page.locator('#copilotPanel')
    expect(panel).to_have_attribute('aria-hidden', 'false')
    expect(panel.locator('.ai-group').first).to_be_visible()
    page.keyboard.press('Escape')
    expect(panel).to_have_attribute('aria-hidden', 'true')
    # Patient 360 and the EMR form with local smart diagnosis lookup
    page.goto(server_url + '/clinical/patient/1')
    expect(page.locator('.patient-header-name, h1').first).to_contain_text('Demo Patient')
    page.goto(server_url + '/doctor/patients/1/emr/add')
    dx = page.locator('#diagnosis')
    dx.fill('hyper')
    menu = page.locator('.dx-menu')
    expect(menu).to_be_visible()
    expect(menu.locator('.dx-item').first).to_contain_text('I10')
    menu.locator('.dx-item').first.click()
    expect(dx).to_have_value(re.compile(r'hypertension', re.I))
    expect(page.locator('#icd10')).to_have_value('I10')


@pytest.mark.e2e
def test_patient_portal_is_plain_and_own_record_only(login, server_url):
    page = login('patient')
    expect(page).to_have_url(re.compile(r'/patient/dashboard'))
    expect(page.locator('.portal-card').first).to_be_visible()
    expect(page.locator('text=MY HEALTH').first).to_be_visible()
    # the patient has no tasks icon and no staff navigation
    expect(page.locator('a[href="/tasks/my-tasks"]')).to_have_count(0)
    expect(page.locator('text=Clinical Workbench')).to_have_count(0)
    page.goto(server_url + '/patient/health-summary')
    expect(page.locator('h1').first).to_be_visible()
    # another patient's chart is refused
    r = page.goto(server_url + '/clinical/patient/2')
    assert r.status in (302, 403)


@pytest.mark.e2e
def test_pharmacist_queue_safety_and_interaction_check(login, server_url):
    page = login('pharmacist')
    expect(page).to_have_url(re.compile(r'/pharmacy/dashboard'))
    page.goto(server_url + '/pharmacy/prescriptions')
    first = page.locator('a:has-text("Safety check")').first
    expect(first).to_be_visible()
    first.click()
    expect(page).to_have_url(re.compile(r'/pharmacy/prescriptions/\d+'))
    expect(page.locator('.page-title').first).to_be_visible()
    expect(page.locator('.patient-safety-strip, .patient-header, .safety-chip').first).to_be_visible()
    page.goto(server_url + '/pharmacy/drug-check')
    boxes = page.locator('input[name=medications]')
    boxes.nth(0).check(); boxes.nth(1).check(); boxes.nth(2).check()
    page.click('button[type=submit]')
    page.wait_for_load_state('networkidle')
    expect(page.locator('text=/interactions?|No interactions detected/').first).to_be_visible()


@pytest.mark.e2e
def test_keyboard_and_rtl(login, server_url):
    page = login('physician')
    page.keyboard.press('Control+Shift+A')
    expect(page.locator('#copilotPanel')).to_have_attribute('aria-hidden', 'false')
    page.keyboard.press('Escape')
    expect(page.locator('#copilotPanel')).to_have_attribute('aria-hidden', 'true')
    # skip link is the first focusable element on a fresh page
    page.goto(server_url + '/doctor/dashboard')
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement.classList.contains('skip-link')")
    # Arabic: RTL document, sidebar on the right
    page.goto(server_url + '/doctor/dashboard?lang=ar')
    assert page.evaluate("document.documentElement.getAttribute('dir')") == 'rtl'
    box = page.locator('#appSidebar').bounding_box()
    width = page.evaluate('window.innerWidth')
    assert box['x'] > width / 2
    expect(page.locator('.ai-strip-title').first).to_contain_text('نماذج')
