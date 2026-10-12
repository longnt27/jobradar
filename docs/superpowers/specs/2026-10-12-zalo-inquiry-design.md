# Approved Zalo inquiry after Facebook post review

## Purpose and boundary

For a relevant Facebook recruiting post that gives a Zalo phone number, Job Radar prepares a short first-contact inquiry. The candidate reviews the recipient and exact message, corrects awkward extracted position wording, and approves one automatic send from their personal Zalo account. This contact asks for the detailed job description and whether the CV can be sent in the chat. It sends no CV or other attachment. A sent inquiry means **contacted, awaiting reply**, not **applied**; the normal application workflow remains available afterward.

The initial message is deterministic and does not call a drafting provider. Its Vietnamese default is exactly three sentences:

> Em chào anh/chị, em thấy bài đăng tuyển vị trí {position} trong nhóm Facebook {group}.
> Anh/chị có thể chia sẻ JD chi tiết cho em được không ạ?
> Em có thể gửi CV qua Zalo cho anh/chị được không ạ?

The candidate can change the reusable template in settings and edit the text of each inquiry before approving it. The default uses “em” throughout. A draft snapshots the current template; changing the setting affects new drafts only. `position` starts from the selected observation's saved title, with the vacancy title as a fallback; `group` comes from that observation's source name. The position label is always editable, and an empty label blocks approval. The exact rendered text is the approved payload.

## Recipient discovery and review

The feature starts from a specific Facebook observation, since one vacancy may be posted by multiple people in different groups. Job Radar reads that observation's saved post text, URL, title, and source group name. It proposes phone numbers only when the same or adjacent line presents them as a Zalo contact; a bare phone number is not enough to choose Zalo automatically. The candidate can edit or select the proposed number, including entering a number from the post when extraction misses it. Each number is normalized for lookup while retaining the original text as evidence.

Before enabling approval, Job Radar opens the signed-in Zalo Web session in its saved Chrome profile and searches the selected number without sending anything. The review shows the number, any Zalo profile name visible in the search result, the Facebook group and post link, an excerpt containing the contact instruction, an editable position label, and the exact message. If the account is signed out, no unique recipient is found, or the interface does not offer a chat, the review explains the blocker and does not enable automatic send. It does not issue a friend request or choose among ambiguous recipients.

The candidate may edit the position label and regenerate the deterministic message, then edit the final message directly. Regeneration never overwrites a manually edited message without an explicit action. One **Approve and send Zalo inquiry** action binds the selected observation, normalized number, resolved recipient identity, and final message text in a package hash. Editing any of those values requires a fresh review before sending.

## Sending and state

Job Radar adds a Zalo connection step to the existing social sign-in setup and uses the existing browser lock and persistent Chrome profile. Zalo's public developer products describe Official Account and business-template messaging, while Zalo supports personal messaging on the web; no supported personal-account send API was identified for this flow. Therefore, this feature uses the visible signed-in Zalo Web interface through Playwright. It does not call undocumented messaging endpoints. [Zalo developer products](https://developers.zalo.me/), [Zalo Web availability](https://help.zalo.me/tai-zalo/).

After the candidate approves the reviewed package, the send worker reopens the same recipient, checks that the chat still matches the approved target, enters the approved text, and sends it once. It records a confirmed send only when a new outgoing message with that exact text appears in the intended conversation after the send action with a sent indicator. The receipt stores the recipient identity, message hash, send time, and visible status; an optional screenshot is cropped to the new outgoing message rather than the whole chat. A login prompt, changed recipient, privacy restriction, unavailable chat, or missing send control stops before the send and requests attention.

An attempt is recorded before the send action. Once the action could have reached Zalo, a timeout, browser crash, or missing confirmation produces an **uncertain** outcome; Job Radar must not retry automatically. The candidate is directed to inspect the conversation and resolve the attempt manually. Duplicate initial inquiries to the same vacancy and normalized phone number are blocked while an attempt is confirmed or uncertain. The system does not send follow-up messages automatically.

Zalo inquiries have separate draft and attempt records linked to the vacancy and Facebook observation. The draft holds the editable number, position label, and message. The attempt holds the immutable approved payload, hash, recipient evidence, status, and receipt or error. Application submissions and the vacancy's applied state are not changed by a Zalo inquiry. The job view displays **contacted, awaiting reply** when an inquiry is confirmed and offers an **Open Zalo Web** control with the contact number, so the candidate can find the conversation again. A later CV submission uses the existing application review and approval flow.

## Implementation boundaries and verification

The feature touches Facebook contact extraction, Zalo connection setup, inquiry draft and attempt storage, review UI and API, and a dedicated Zalo Web adapter. The adapter owns recipient lookup and send verification; the inquiry service owns approval hashes, duplicate protection, and state transitions. This keeps Zalo page selectors out of the application submission code.

Tests cover phone extraction only from explicit Zalo instructions, multiple-number ambiguity, deterministic template rendering, correction of bad position text, approval invalidation after edits, recipient mismatch, confirmed and uncertain sends, duplicate prevention, and the fact that an inquiry never marks a vacancy applied. Browser interaction tests use a local Zalo-like fixture; a signed-in live preflight verifies selectors without sending. A real message is sent only for a candidate-approved inquiry. If Zalo Web changes or blocks a step, the feature reports that condition instead of claiming success.
