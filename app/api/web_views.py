from fastapi import APIRouter, Request, Cookie
from fastapi.responses import HTMLResponse
from typing import Optional

from app.subscribers.canonical import CANONICAL_BRANCH_DISPLAY
from app.api.session import verify_session_token
from app.config import settings
from app.database.repository import DB_PATH
from app.database.sqlite_repository import SQLiteUserRepository

router = APIRouter(include_in_schema=False)

@router.get("/", response_class=HTMLResponse)
@router.get("/signup", response_class=HTMLResponse)
def signup_page():
    branch_options = "\n".join(
        f'<option value="{k.value}">{v}</option>' 
        for k, v in CANONICAL_BRANCH_DISPLAY.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>VIT TPO Watcher — Class of 2028</title>
    <style>
        :root {{
            --primary: #0366d6;
            --primary-hover: #0255b3;
            --bg: #f6f8fa;
            --card-bg: #ffffff;
            --border: #d0d7de;
            --text: #1f2328;
            --text-muted: #656d76;
            --accent: #2da44e;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 40px 16px;
            display: flex;
            justify-content: center;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            max-width: 520px;
            width: 100%;
            padding: 32px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }}
        .badge {{
            display: inline-block;
            background-color: #ddf4ff;
            color: #0969da;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 12px;
        }}
        h1 {{
            font-size: 24px;
            margin: 0 0 8px 0;
            color: #111;
        }}
        p.subtitle {{
            color: var(--text-muted);
            font-size: 14px;
            margin: 0 0 24px 0;
            line-height: 1.5;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 6px;
        }}
        input[type="email"], select {{
            width: 100%;
            padding: 10px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 14px;
            box-sizing: border-box;
            background-color: #fff;
        }}
        input[type="email"]:focus, select:focus {{
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(3, 102, 214, 0.2);
        }}
        .checkbox-group {{
            background: #fbfcfd;
            border: 1px solid #eaeef2;
            border-radius: 8px;
            padding: 14px;
            margin-top: 8px;
        }}
        .checkbox-item {{
            display: flex;
            align-items: center;
            margin-bottom: 10px;
            font-size: 14px;
        }}
        .checkbox-item:last-child {{
            margin-bottom: 0;
        }}
        .checkbox-item input {{
            margin-right: 10px;
            width: 16px;
            height: 16px;
            accent-color: var(--primary);
        }}
        button.btn {{
            width: 100%;
            background-color: var(--accent);
            color: white;
            border: none;
            padding: 12px;
            border-radius: 6px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s ease;
        }}
        button.btn:hover {{
            background-color: #2c974b;
        }}
        .footer-links {{
            text-align: center;
            margin-top: 24px;
            font-size: 13px;
            color: var(--text-muted);
        }}
        .footer-links a {{
            color: var(--primary);
            text-decoration: none;
        }}
        .alert-box {{
            display: none;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .alert-success {{
            background-color: #dafbe1;
            color: #1a7f37;
            border: 1px solid #aceebb;
        }}
        .alert-error {{
            background-color: #ffebe9;
            color: #cf222e;
            border: 1px solid #ff8182;
        }}
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">VIT Pune • Batch 2028</span>
        <h1>TPO Opportunity Watcher</h1>
        <p class="subtitle">Receive autonomous email alerts the instant a new internship or placement is posted on your VIT TPO portal.</p>

        <div id="alertBox" class="alert-box"></div>

        <form id="signupForm">
            <div class="form-group">
                <label for="email">Student Email Address</label>
                <input type="email" id="email" required placeholder="firstname.lastname@vit.edu"/>
            </div>

            <div class="form-group">
                <label for="gradYear">Graduation Year</label>
                <select id="gradYear" disabled>
                    <option value="2028" selected>2028 (Restricted to Class of 2028)</option>
                </select>
            </div>

            <div class="form-group">
                <label for="branch">Canonical Branch / Program</label>
                <select id="branch" required>
                    <option value="" disabled selected>Select your engineering branch...</option>
                    {branch_options}
                </select>
            </div>

            <div class="form-group">
                <label>Opportunity Alert Preferences</label>
                <div class="checkbox-group">
                    <label class="checkbox-item">
                        <input type="checkbox" id="prefInternship" checked/>
                        <span>Internships</span>
                    </label>
                    <label class="checkbox-item">
                        <input type="checkbox" id="prefPPO" checked/>
                        <span>Internship + Performance-based PPO</span>
                    </label>
                    <label class="checkbox-item">
                        <input type="checkbox" id="prefPlacement" checked/>
                        <span>Full-time Placements</span>
                    </label>
                </div>
            </div>

            <button type="submit" class="btn" id="submitBtn">Subscribe for Alerts</button>
        </form>

        <div class="footer-links">
            Already subscribed? <a href="/preferences">Manage your preferences</a>
        </div>
    </div>

    <script>
        const form = document.getElementById('signupForm');
        const alertBox = document.getElementById('alertBox');
        const submitBtn = document.getElementById('submitBtn');

        form.addEventListener('submit', async (e) => {{
            e.preventDefault();
            submitBtn.disabled = true;
            submitBtn.textContent = 'Registering...';
            alertBox.style.display = 'none';

            const payload = {{
                email: document.getElementById('email').value.trim(),
                graduation_year: 2028,
                branch_canonical: document.getElementById('branch').value,
                pref_internship: document.getElementById('prefInternship').checked,
                pref_placement: document.getElementById('prefPlacement').checked,
                pref_ppo: document.getElementById('prefPPO').checked
            }};

            try {{
                const res = await fetch('/api/v1/auth/signup', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await res.json();

                alertBox.style.display = 'block';
                if (res.ok) {{
                    alertBox.className = 'alert-box alert-success';
                    alertBox.textContent = data.message || 'Verification link sent to your email!';
                    form.reset();
                }} else {{
                    alertBox.className = 'alert-box alert-error';
                    alertBox.textContent = data.detail || 'Registration failed. Please check inputs.';
                }}
            }} catch (err) {{
                alertBox.style.display = 'block';
                alertBox.className = 'alert-box alert-error';
                alertBox.textContent = 'Network error. Please try again.';
            }} finally {{
                submitBtn.disabled = false;
                submitBtn.textContent = 'Subscribe for Alerts';
            }}
        }});
    </script>
</body>
</html>"""

@router.get("/preferences", response_class=HTMLResponse)
def preferences_page(tpo_session: Optional[str] = Cookie(None)):
    user_id = verify_session_token(tpo_session, settings.SECRET_KEY) if tpo_session else None

    if not user_id:
        # User not logged in: show request link form
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>Manage Preferences — VIT TPO Watcher</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: #f6f8fa; margin: 0; padding: 40px 16px; display: flex; justify-content: center; }
        .card { background: #fff; border: 1px solid #d0d7de; border-radius: 12px; max-width: 480px; width: 100%; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
        h1 { font-size: 22px; margin: 0 0 10px 0; color: #111; }
        p { color: #57606a; font-size: 14px; line-height: 1.5; margin: 0 0 20px 0; }
        input[type="email"] { width: 100%; padding: 10px 12px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 14px; box-sizing: border-box; margin-bottom: 16px; }
        button { width: 100%; background: #0366d6; color: #fff; border: none; padding: 12px; border-radius: 6px; font-size: 15px; font-weight: 600; cursor: pointer; }
        .msg { display: none; padding: 12px; border-radius: 6px; font-size: 14px; margin-bottom: 16px; background: #dafbe1; color: #1a7f37; border: 1px solid #aceebb; }
        a { color: #0366d6; text-decoration: none; font-size: 13px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Manage Your Preferences</h1>
        <p>For your security, we do not use passwords. Enter your registered email to receive a single-use, 15-minute access link.</p>
        <div id="msg" class="msg"></div>
        <form id="reqForm">
            <input type="email" id="email" required placeholder="firstname.lastname@vit.edu"/>
            <button type="submit" id="btn">Send Secure Access Link</button>
        </form>
        <p style="margin-top: 24px; text-align: center;"><a href="/">← Return to Signup</a></p>
    </div>
    <script>
        document.getElementById('reqForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('btn');
            btn.disabled = true;
            btn.textContent = 'Sending...';
            const email = document.getElementById('email').value.trim();
            try {
                const res = await fetch('/api/v1/preferences/request-link', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const msg = document.getElementById('msg');
                msg.style.display = 'block';
                msg.textContent = 'If an account exists, a 15-minute login link has been sent to your inbox!';
                document.getElementById('reqForm').reset();
            } finally {
                btn.disabled = false;
                btn.textContent = 'Send Secure Access Link';
            }
        });
    </script>
</body>
</html>"""

    # User is logged in! Load their data
    user_repo = SQLiteUserRepository(DB_PATH)
    user = user_repo.get_by_id(user_id)
    prefs = user_repo.get_preferences(user_id)

    if not user or not prefs:
        return "<p>Session expired. Please request a new link.</p>"

    branch_display = CANONICAL_BRANCH_DISPLAY.get(user["branch_canonical"], user["branch_canonical"])
    chk_intern = "checked" if prefs["pref_internship"] else ""
    chk_ppo = "checked" if prefs["pref_ppo"] else ""
    chk_place = "checked" if prefs["pref_placement"] else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>Edit Preferences — VIT TPO Watcher</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: #f6f8fa; margin: 0; padding: 40px 16px; display: flex; justify-content: center; }}
        .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 12px; max-width: 480px; width: 100%; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        h1 {{ font-size: 22px; margin: 0 0 6px 0; color: #111; }}
        .user-info {{ background: #f6f8fa; border: 1px solid #eaeef2; border-radius: 6px; padding: 12px; margin-bottom: 20px; font-size: 13px; color: #444; }}
        .checkbox-item {{ display: flex; align-items: center; margin-bottom: 12px; font-size: 14px; }}
        .checkbox-item input {{ margin-right: 10px; width: 16px; height: 16px; }}
        button.btn {{ width: 100%; background: #2da44e; color: #fff; border: none; padding: 12px; border-radius: 6px; font-size: 15px; font-weight: 600; cursor: pointer; }}
        .msg {{ display: none; padding: 10px; border-radius: 6px; font-size: 14px; margin-bottom: 16px; background: #dafbe1; color: #1a7f37; border: 1px solid #aceebb; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Notification Preferences</h1>
        <div class="user-info">
            <strong>Email:</strong> {user['email']}<br/>
            <strong>Batch:</strong> {user['graduation_year']}<br/>
            <strong>Branch:</strong> {branch_display}
        </div>

        <div id="saveMsg" class="msg">Preferences updated successfully!</div>

        <form id="prefForm">
            <div style="margin-bottom: 24px;">
                <label class="checkbox-item">
                    <input type="checkbox" id="prefInternship" {chk_intern}/>
                    <span>Notify for Internships</span>
                </label>
                <label class="checkbox-item">
                    <input type="checkbox" id="prefPPO" {chk_ppo}/>
                    <span>Notify for Internship + PPO</span>
                </label>
                <label class="checkbox-item">
                    <input type="checkbox" id="prefPlacement" {chk_place}/>
                    <span>Notify for Full-time Placements</span>
                </label>
            </div>
            <button type="submit" class="btn" id="saveBtn">Save Preferences</button>
        </form>
    </div>

    <script>
        document.getElementById('prefForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = document.getElementById('saveBtn');
            btn.disabled = true;
            btn.textContent = 'Saving...';
            const payload = {{
                pref_internship: document.getElementById('prefInternship').checked,
                pref_placement: document.getElementById('prefPlacement').checked,
                pref_ppo: document.getElementById('prefPPO').checked
            }};
            try {{
                const res = await fetch('/api/v1/preferences', {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                if (res.ok) {{
                    document.getElementById('saveMsg').style.display = 'block';
                }}
            }} finally {{
                btn.disabled = false;
                btn.textContent = 'Save Preferences';
            }}
        }});
    </script>
</body>
</html>"""
