# Introduction to UAPI

## Overview

UAPI accesses the cPanel interface's features. Use this API to access and modify cPanel account data and settings.

* If you encounter problems with a local access method (LiveAPI PHP or LiveAPI Perl), attempt a [browser-based call](/cpanel/introduction/#cpanel-or-webmail-session-url-1).
* You cannot call this API's functions via another API's call methods.
* When you enable a non-Standard Node [server profile](https://go.cpanel.net/serverroles), the system disables API calls associated with that profile's disabled roles.


## Basic Usage

### cPanel or Webmail Session URL


```bash
/execute/Module/function?parameter=value&parameter=value&parameter=value
```

> **Note**
For more information about browser-based UAPI calls, read the [cPanel or Webmail Session URL calls](/cpanel/introduction/#cpanel-or-webmail-session-url-1) section below.


This example uses the following variables:

| Variable | Description | Possible values | Example |
|  --- | --- | --- | --- |
| `function` | The UAPI function. | Any UAPI function in the specified module. | `addpop` |
| `Module` | The function's module. | Any UAPI module. | `Email` |
| `parameters` | The function's input parameters. | Any parameter from the specified function. | `domain` |
| `value` | The value to assign to the input parameter. | Parameters accept a variety of values. | `example.com` |


### LiveAPI PHP Class


```php
$cpanel = new CPANEL(); // Connect to cPanel - only do this once.

// Call a UAPI function.
$function_result = $cpanel->uapi(
    'Module', 'function',
    array(
        'parameter'     => 'value',
        'parameter'     => 'value',
        'parameter'     => 'value',
         )
);
```

> **Note**
* For more information, read our [Guide to the LiveAPI System - PHP Class](/guides/guide-to-the-liveapi-system/guide-to-the-liveapi-system-php-class).
* For more information about browser-based UAPI calls, read the [cPanel or Webmail Session URL calls](/cpanel/introduction/#cpanel-or-webmail-session-url-1) below.



This example uses the following variables:

| Variable | Description | Possible values | Example |
|  --- | --- | --- | --- |
| `function` | The UAPI function. | Any UAPI function in the specified module. | `addpop` |
| `Module` | The function's module. | Any UAPI module. | `Email` |
| `parameters` | The function's input parameters. | Any parameter from the specified function. | `domain` |
| `value` | The value to assign to the input parameter. | Parameters accept a variety of values. | `example.com` |


### LiveAPI Perl Class


```perl
#!/usr/bin/perl

# Instantiate the Cpanel::LiveAPI object.
use Cpanel::LiveAPI ();

# Connect to cPanel - only do this once.
my $cpliveapi = Cpanel::LiveAPI->new();

# Call a UAPI function.
my $function_result = $cpliveapi->uapi(
    'Module', 'function',
    {
        'parameter'     => 'value',
        'parameter'     => 'value',
        'parameter'     => 'value',
    }
);

# Perform the desired actions.

# Disconnect from cPanel - only do this once.
$cpliveapi->end();
```

> **Note**
* For more information, read our [Guide to the LiveAPI System - Perl Module](/guides/guide-to-the-liveapi-system/guide-to-the-liveapi-system-perl-module/).
* For more information about browser-based UAPI calls, read the [cPanel or Webmail Session URL calls](/cpanel/introduction/#cpanel-or-webmail-session-url-1) below.



This example uses the following variables:

| Variable | Description | Possible values | Example |
|  --- | --- | --- | --- |
| `function` | The UAPI function. | Any UAPI function in the specified module. | `addpop` |
| `Module` | The function's module. | Any UAPI module. | `Email` |
| `parameters` | The function's input parameters. | Any parameter from the specified function. | `domain` |
| `value` | The value to assign to the input parameter. | Parameters accept a variety of values. | `example.com` |


### Command Line


```bash
uapi  --user=username --output=type Module function parameter=value parameter=value
```

> **Note**
* UAPI calls via the command line do not return the metadata that other methods return if they experience errors that prevent a successful function run. For more information, read our Guide to UAPI - Return Data documentation.
* cPanel & WHM introduced the command line feature in version 56.



UAPI calls through the command line consist of the following basic parts:

![Command API Example](/assets/uapi-command.be3f98b9253d7531d23bfde756aed1a34bcee06695dd5126020dc076d21ab120.e19a1923.png)

#### Command

This value is always `uapi` for calls to UAPI. If you run CloudLinux™, you **must** use the full path of the `uapi` command:`/usr/local/cpanel/bin/uapi`

#### User

The account-level username.

#### Output Type

The API output type that you wish to receive.

* Use `--output=json` to return JSON-formatted output.
* Use `--output=jsonpretty` to return indented JSON-formatted output.
* Use `--output=yaml` to return YAML-formatted output.


This parameter defaults to `--output=yaml`.

#### Module

The UAPI module name.

#### Function

The UAPI function.

#### User

The cPanel account username.

#### Input parameters and values

The function's input parameters and their values.

* Space-separate multiple parameter=value pairs.
* Special characters within a key's value may cause an error.
* You must either escape any special characters within values or surround the value with appropriate quotes.
* For more information, read Wikipedia's [Escape Characters](https://en.wikipedia.org/wiki/Escape_character) article.
* For example, a bash shell command with a JSON-encoded value may appear similar to one of the following examples:



```bash
uapi --user username Module function key=[\"sslinstall\",\"videotut\"]"
uapi --user username Module function key='{"videotut","sslinstall"}'
```

> **Note**
The term "Boolean" in our documentation refers to parameters that accept values of `1` or `0`. cPanel & WHM's APIs do not support the literal values of true and false.


For more information about this feature, run the following command:


```bash
uapi --help
```

## cPanel or Webmail Session URL

You can access UAPI functions directly from any web browser when you make the call from a cPanel or Webmail session.

Browser-based calls consist of the following basic parts:

![JSON API Example](/assets/uapi.e39acceef3a34d84c5f02546f9ed5f14761646239324702f3ebd446a5b1bd449.e19a1923.jpg)

#### Server and port

The web server's HTTP address and the port number to use. Generally, this value begins with `https://` and the domain name.

When you call this API, use the following ports:

* `2082` - Unsecure calls as a specific cPanel account.
* `2083` - Secure calls as a specific cPanel account.
* `2095` - Unsecure calls via a Webmail session.
* `2096` - Secure calls via a Webmail session.


> **Warning**
Unless you call this API via the WHM API 1, you **cannot** call this API via WHM's ports (`2086` or `2087`).


#### Security token

The current session's [security token](https://go.cpanel.net/basic-security-concepts).

> **Note**
Cookie-based calls (for example, calls from a web browser) require a security token. For other authentication methods, read our Guide to API Authentication documentation.


#### Action

This portion of the URL will always be `execute`.

#### Module

The UAPI module name.

#### Function

The UAPI function.

#### Input parameters and values

The UAPI function's input parameters and their values.

* Separate multiple `parameter=value` pairs with the ampersand character (`&`).
* You must [URI-encode](https://en.wikipedia.org/wiki/Percent-encoding) these values.


> **Note**
The term "Boolean" in our documentation refers to parameters that accept values of `1` or `0`. cPanel & WHM's APIs do **not** support the literal values of `true` and `false`.


Do not attempt to use cPanel or WHM interface URLs to perform actions in custom code. You **must** call the appropriate API functions in order to perform the actions of cPanel & WHM's interfaces.

For example, do **not** pass values to `.html` pages, as in the following example:


```
https://example.com:2083/cpsess###########/frontend/jupiter/email_accounts/index.html/create#email=test&domain=example.com
```

While this **unsupported** method sometimes worked in previous versions of cPanel & WHM, we **strongly** discourage its use and do **not** guarantee that it will work in the future. Instead, the correct method to perform this action is to call the appropriate API function.