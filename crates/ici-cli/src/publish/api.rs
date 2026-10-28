//! Bounded REST operations. Credentials are neither formatted nor attached to errors.
use base64::Engine;
use ici_distribution::transport::{Request, Response, TransportError};
use serde_json::{Value, json};
use std::{collections::BTreeMap, time::Duration};

pub struct Target {
    pub api_url: String,
    pub repo: String,
    pub branch: String,
    pub token: String,
}
impl Target {
    pub fn validate(&self) -> Result<(), String> {
        let authority = self
            .api_url
            .strip_prefix("https://")
            .and_then(|url| url.split('/').next());
        if authority.is_none_or(|host| host.is_empty())
            || self.api_url.contains(['@', '?', '#', '\\'])
            || self.api_url.chars().any(char::is_whitespace)
            || self.api_url.len() > 4096
        {
            return Err(
                "publish requires an explicit HTTPS API URL without credentials, query or fragment"
                    .into(),
            );
        }
        let pieces: Vec<_> = self.repo.split('/').collect();
        if pieces.len() != 2
            || pieces.iter().any(|piece| {
                piece.is_empty()
                    || matches!(*piece, "." | "..")
                    || !piece
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
            })
        {
            return Err("publish repository must be owner/name".into());
        }
        if self.branch.is_empty()
            || self.branch.len() > 512
            || self.branch.chars().any(char::is_control)
            || self.token.is_empty()
            || self.token.chars().any(char::is_control)
        {
            return Err("publish branch or token is missing or malformed".into());
        }
        Ok(())
    }
}
pub fn quote(value: &str) -> String {
    percent_encoding::utf8_percent_encode(value, percent_encoding::NON_ALPHANUMERIC).to_string()
}
pub struct Client<'a, F> {
    pub target: &'a Target,
    pub send: &'a mut F,
}
impl<F: FnMut(&Request) -> Result<Response, TransportError>> Client<'_, F> {
    pub fn api(
        &mut self,
        method: &str,
        path: &str,
        payload: Option<Value>,
    ) -> Result<(u16, Value), String> {
        let request = Request {
            method: method.into(),
            url: format!(
                "{}/repos/{}{}",
                self.target.api_url.trim_end_matches('/'),
                self.target.repo,
                path
            ),
            headers: BTreeMap::from([
                (
                    "Authorization".into(),
                    format!("Bearer {}", self.target.token),
                ),
                ("Accept".into(), "application/vnd.github+json".into()),
                ("X-GitHub-Api-Version".into(), "2022-11-28".into()),
                ("Content-Type".into(), "application/json".into()),
            ]),
            body: payload
                .map(|value| serde_json::to_vec(&value))
                .transpose()
                .map_err(|_| "cannot serialize publish request")?
                .unwrap_or_default(),
            max_bytes: 8 * 1024 * 1024,
            timeout: Duration::from_secs(30),
        };
        let response = (self.send)(&request).map_err(|error| error.to_string())?;
        let value = if response.bytes.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&response.bytes).map_err(|_| {
                format!(
                    "publish endpoint returned invalid JSON (HTTP {})",
                    response.status
                )
            })?
        };
        Ok((response.status, value))
    }
    pub fn ensure_branch(&mut self, base: &str) -> Result<(), String> {
        let (status, _) = self.api(
            "GET",
            &format!(
                "/git/ref/{}",
                quote(&format!("heads/{}", self.target.branch))
            ),
            None,
        )?;
        if status == 200 {
            return Ok(());
        }
        if status != 404 {
            return Err(format!("cannot inspect publish branch (HTTP {status})"));
        }
        let mut sha = base.to_owned();
        if sha.is_empty() {
            let (status, repo) = self.api("GET", "", None)?;
            if status != 200 {
                return Err(format!("cannot inspect publish repository (HTTP {status})"));
            }
            let branch = repo["default_branch"]
                .as_str()
                .ok_or("repository has no default branch")?;
            let (status, reference) = self.api(
                "GET",
                &format!("/git/ref/{}", quote(&format!("heads/{branch}"))),
                None,
            )?;
            if status != 200 {
                return Err(format!("cannot inspect default branch (HTTP {status})"));
            }
            sha = reference["object"]["sha"]
                .as_str()
                .ok_or("default branch has no commit")?
                .into();
        }
        let (status, _) = self.api(
            "POST",
            "/git/refs",
            Some(json!({"ref":format!("refs/heads/{}",self.target.branch),"sha":sha})),
        )?;
        if matches!(status, 200 | 201) {
            Ok(())
        } else {
            Err(format!("cannot create publish branch (HTTP {status})"))
        }
    }
    pub fn put(&mut self, path: &str, bytes: &[u8]) -> Result<(), String> {
        let encoded = path.split('/').map(quote).collect::<Vec<_>>().join("/");
        for _ in 0..3 {
            let (status, previous) = self.api(
                "GET",
                &format!("/contents/{encoded}?ref={}", quote(&self.target.branch)),
                None,
            )?;
            if !matches!(status, 200 | 404) {
                return Err(format!("cannot inspect remote report (HTTP {status})"));
            }
            let mut body = json!({"message":format!("ici: publish {path}"),"content":base64::engine::general_purpose::STANDARD.encode(bytes),"branch":self.target.branch});
            if status == 200 {
                body["sha"] = previous
                    .get("sha")
                    .filter(|sha| sha.is_string())
                    .cloned()
                    .ok_or("remote report has no blob identity")?;
            }
            let (status, _) = self.api("PUT", &format!("/contents/{encoded}"), Some(body))?;
            if matches!(status, 200 | 201) {
                return Ok(());
            }
            if status != 409 {
                return Err(format!("report upload failed (HTTP {status})"));
            }
        }
        Err("report upload conflicted after three bounded attempts".into())
    }
    pub fn pages(&mut self) -> Result<Option<String>, String> {
        let (status, value) = self.api("GET", "/pages", None)?;
        Ok((status == 200)
            .then(|| {
                value["html_url"]
                    .as_str()
                    .filter(|url| url.starts_with("https://"))
                    .map(str::to_owned)
            })
            .flatten())
    }
    pub fn head(&mut self, pr: u64) -> Result<String, String> {
        let (status, value) = self.api("GET", &format!("/pulls/{pr}"), None)?;
        if status != 200 {
            return Err(format!("cannot verify pull request head (HTTP {status})"));
        }
        value["head"]["sha"]
            .as_str()
            .map(str::to_owned)
            .ok_or("pull request head is missing".into())
    }
    pub fn comment(&mut self, pr: u64, marker: &str) -> Result<Option<(u64, String)>, String> {
        for page in 1..=20 {
            let (status, value) = self.api(
                "GET",
                &format!("/issues/{pr}/comments?per_page=100&page={page}"),
                None,
            )?;
            if status != 200 {
                return Err(format!("cannot inspect existing comments (HTTP {status})"));
            }
            let rows = value.as_array().ok_or("comment response is not a list")?;
            for row in rows {
                if let Some(body) = row["body"].as_str().filter(|body| body.contains(marker)) {
                    return Ok(Some((
                        row["id"].as_u64().ok_or("comment has no identity")?,
                        body.into(),
                    )));
                }
            }
            if rows.len() < 100 {
                return Ok(None);
            }
        }
        Err("comment lookup exceeded 2000 comments; refusing to create a duplicate".into())
    }
    pub fn write_comment(
        &mut self,
        pr: u64,
        id: Option<u64>,
        body: &str,
    ) -> Result<String, String> {
        let (method, path) = id.map_or_else(
            || ("POST", format!("/issues/{pr}/comments")),
            |id| ("PATCH", format!("/issues/comments/{id}")),
        );
        let (status, value) = self.api(method, &path, Some(json!({"body":body})))?;
        if !matches!(status, 200 | 201) {
            return Err(format!("comment update failed (HTTP {status})"));
        }
        value["html_url"]
            .as_str()
            .map(str::to_owned)
            .ok_or("comment response has no URL".into())
    }
}
