# Email Accounts

Email / Email Accounts

## Import email accounts from CSV file

 - [GET /CSVImport/doimport](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/csvimport-doimport.md): This function imports email accounts from an already uploaded CSV file.

## Return current user's account name

 - [GET /Email/account_name](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-account_name.md): This function returns the provided value. This function works with other functions to display form data within a user interface.

Note:

  If you call this function from a Webmail session URL, the system will only access data for that email account.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Create email account's autoresponder

 - [GET /Email/add_auto_responder](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-add_auto_responder.md): This function creates an autoresponder for an email account.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Create email account

 - [GET /Email/add_pop](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-add_pop.md): This function creates an email address.

Important:

   When you disable the Receive Mail* role, the system disables this function.
   We recommend that you use the UAPI UserManager::create_user function to create an email address instead of this function. This function is incompatible with the Reset Password* feature.
  * You must URI-encode values when using the CLI.

## Return mail directory's subdirectories and files

 - [GET /Email/browse_mailbox](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-browse_mailbox.md): This function lists the mail directory's subdirectories (boxes) and files.

Important:

  When you disable the Receive Mail role, the system disables this function.

Notes:

  If you do not use any input parameters, the function returns a list of items in the cPanel account's main mail directory.

## Return cPanel account's autoresponders total

 - [GET /Email/count_auto_responders](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-count_auto_responders.md): This function returns the number of autoresponders for every email address on a cPanel account.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Return cPanel account's email account total

 - [GET /Email/count_pops](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-count_pops.md): This function returns the number of email accounts for a cPanel account.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Delete email account's autoresponder

 - [GET /Email/delete_auto_responder](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-delete_auto_responder.md): This function deletes an autoresponder.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Delete email account's outgoing messages

 - [GET /Email/delete_held_messages](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-delete_held_messages.md): This function deletes all outbound email messages held in the mail queue for the specified email account.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Delete email account

 - [GET /Email/delete_pop](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-delete_pop.md): This function deletes an email address.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Send email client settings to an email address

 - [GET /Email/dispatch_client_settings](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-dispatch_client_settings.md): This function sends an email account's client settings to an email address.

Important:

When you disable the Receive Mail role, the system disables this function.

## Update email account's quota

 - [GET /Email/edit_pop_quota](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-edit_pop_quota.md): This function changes an email address's quota.

## Return email account's autoresponder information

 - [GET /Email/get_auto_responder](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_auto_responder.md): This function retrieves autoresponder information.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's client settings

 - [GET /Email/get_client_settings](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_client_settings.md): This function retrieves an email account's client settings.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's default email quota

 - [GET /Email/get_default_email_quota](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_default_email_quota.md): This function retrieves the account's default email quota size, in bytes format.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's default email quota in MiB

 - [GET /Email/get_default_email_quota_mib](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_default_email_quota_mib.md): This function retrieves the account's default email quota size in mebibytes (MiB).

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's disk usage

 - [GET /Email/get_disk_usage](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_disk_usage.md): This function retrieves the disk space that an email account uses.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's outgoing message count

 - [GET /Email/get_held_message_count](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_held_message_count.md): This function returns the count of outbound email messages held in the mail queue for the specified email account.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return primary email account's disk usage

 - [GET /Email/get_main_account_disk_usage](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_main_account_disk_usage.md): This function returns the disk space that the main account uses.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return primary email account's disk usage in bytes

 - [GET /Email/get_main_account_disk_usage_bytes](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_main_account_disk_usage_bytes.md): This function returns the disk space that the cPanel account uses.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's max quota size

 - [GET /Email/get_max_email_quota](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_max_email_quota.md): This function retrieves the account's maximum email quota size, in bytes format.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Return email account's max quota size in MiB

 - [GET /Email/get_max_email_quota_mib](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_max_email_quota_mib.md): This function retrieves the account's maximum email account quota size, in
Mebibytes (MiB) format.

Important:

When you disable the Receive Mail role,
the system disables this function.

## Return email account's quota

 - [GET /Email/get_pop_quota](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_pop_quota.md): This function retrieves an email account's quota.

Important:

When you disable the Receive Mail role, the system disables this function.

## Return email account's Webmail settings

 - [GET /Email/get_webmail_settings](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-get_webmail_settings.md): This function retrieves an email account's Webmail settings.

Important:

When you disable the _Receive Mail_ role, the system disables this function.

## Stop email account's outgoing mail

 - [GET /Email/hold_outgoing](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-hold_outgoing.md): This function sets Exim's queue to not send outgoing mail from an email account.

Notes:

* To send all mail from the queue, use the UAPI Email::release_outgoing function.
* To reject outgoing mail and not place mail in a queue, use the UAPI Email::suspend_outgoing function.
* This function does not hold local outgoing mail.

## Return domain's autoresponders

 - [GET /Email/list_auto_responders](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-list_auto_responders.md): This function lists a domain's autoresponders.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Return domain's default email address

 - [GET /Email/list_default_address](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-list_default_address.md): This function retrieves a domain's default address.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Return cPanel account's mail domains

 - [GET /Email/list_mail_domains](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-list_mail_domains.md): This function lists the account's mail domains.

Important:

  When you disable the Receive Mail role, the system disables this function.

Note:

  This function always returns the account's main domain first.

Additional Note:

  By default, temporary domains (*.cpanel.site) are excluded. Use the return_temporary_domain parameter to include them.

## Return email accounts

 - [GET /Email/list_pops](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-list_pops.md): This function lists the cPanel account's email accounts.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Return email accounts with disk information

 - [GET /Email/list_pops_with_disk](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-list_pops_with_disk.md): This function lists the cPanel account's email accounts with disk information.

Important:

  When you disable the Receive Mail role, the system disables this function.

## Update email account password

 - [GET /Email/passwd_pop](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-passwd_pop.md): This function changes an email account's password.

Important:

When you disable the Receive Mail role, the system disables this function.

## Start email account outgoing mail

 - [GET /Email/release_outgoing](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-release_outgoing.md): This function sends all of the outgoing mail from Exim's queue for an email account.

Note:

To set Exim to queue all outgoing mail for an email account, use the UAPI Email::hold_outgoing function.

## Create default email address

 - [GET /Email/set_default_address](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-set_default_address.md): This function configures a default (catchall) email address.

Important:

  When you disable the Mail Receive role, the system disables this function.

## Add manual MX redirection

 - [GET /Email/set_manual_mx_redirects](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-set_manual_mx_redirects.md): This function lets you create a manual Exim mail exchanger (MX) redirect for a domain.
An MX redirection lets you bypass the domain's MX lookup via the Domain Name System (DNS).
This function adds the manual redirect entries to the /etc/manualmx file.

Note:

  To remove a domain's manual MX redirection, use the UAPI Email unset_manual_mx_redirect function.

## Stop cPanel account IMAP and POP3 connections

 - [GET /Email/terminate_mailbox_sessions](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-terminate_mailbox_sessions.md): This function terminates all IMAP and POP3 connections for a cPanel account.

Note:

This function ends connections for every email address, which includes the default address.

## Run email delivery route trace

 - [GET /Email/trace_delivery](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-trace_delivery.md): This function traces the email delivery route to an email account.

## Remove manual MX redirection

 - [GET /Email/unset_manual_mx_redirects](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-unset_manual_mx_redirects.md): This function lets you create a manual Exim mail exchanger (MX) redirect for a domain.
An MX redirection lets you bypass the domain's MX lookup via the Domain Name System (DNS).
This function adds the manual redirect entries to the /etc/manualmx file.

Note:

  To remove a domain's manual MX redirection, use the UAPI Email unset_manual_mx_redirect function.

## Validate email account password

 - [GET /Email/verify_password](https://api.docs.cpanel.net/specifications/cpanel.openapi/email-accounts/email-verify_password.md): This function verifies the password for an email account.

Important:

When you disable the Receive Mail role, the system disables this function.

