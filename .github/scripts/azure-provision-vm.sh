#!/usr/bin/env bash
# Create the ephemeral runner VM, walking a region × zone × priority matrix
# until one combination accepts the allocation.
#
# Usage:
#   RG=<rg> REGIONS="eastus eastus2 …" SIZE=<sku> PRIORITY=Spot|Regular \
#   CLOUD_INIT=<path> azure-provision-vm.sh
#
# REGIONS is a space-separated ordered list; the first one with capacity wins.
# A single resource group ($RG) is created in the first region of the list and
# holds whichever VM eventually lands — Azure resource groups can host
# resources from any region, so we don't pay for create/delete round-trips on
# capacity misses. The chosen region/zone/priority are echoed in `key=value`
# form on stdout for the caller to reuse.
#
# Spot ARM allocations are gated by three separate quotas per region:
#   lowPriorityCores            spot vCPUs (checked for Spot)
#   cores                       regional vCPUs, all SKUs (checked for Regular)
#   <Family>Family (e.g. standardFalsv7Family)  per-family vCPUs (Regular only)
# Spot skips both the regional and per-family buckets, so a Spot attempt only
# needs lowPriorityCores room. Checking the SKU's vCPU count against
# lowPriorityCores before submitting avoids burning a region × zone sweep on a
# combination Azure rejects during template preflight in a few seconds.
set -euo pipefail

: "${RG:?RG required}"
: "${REGIONS:?REGIONS required}"
: "${SIZE:?SIZE required}"
: "${PRIORITY:?PRIORITY required}"
: "${CLOUD_INIT:?CLOUD_INIT path required}"
ZONES=${ZONES:-"2 1 3"}
# Disk size is a build-input: the LLVM + MinGW build needs 256 GB, while a job
# that only runs test cases gets by with the default (~30 GB plus room for the
# case work directory and the SDK archive).
OS_DISK_SIZE_GB=${OS_DISK_SIZE_GB:-256}
VM_NAME=${VM_NAME:-runner}

# A typo here silently provisions a full-price VM, so reject it rather than
# letting the Spot block be skipped.
case "$PRIORITY" in
Spot | Regular) ;;
*)
	echo "::error::PRIORITY must be Spot or Regular, got '$PRIORITY'." >&2
	exit 1
	;;
esac

read -ra region_arr <<<"$REGIONS"
# read -ra, not a bare loop over $ZONES: an override containing a glob character
# would otherwise expand against the working directory and feed az a filename.
read -ra zone_arr <<<"$ZONES"
home_region="${region_arr[0]}"

# Tag the RG so azure-reap-orphans.sh can find stale ones cheaply via az group list.
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
az group create -n "$RG" -l "$home_region" --tags "createdAt=$created_at" -o none

# From here on the group exists. Any exit that is not a successful allocation
# must remove it, or the group is orphaned until the hourly reaper notices.
vm_allocated=0
cleanup_group() {
	local rc=$?
	if ((rc != 0)) && ((vm_allocated == 0)); then
		az group delete -n "$RG" --yes --no-wait >/dev/null 2>&1 || true
	fi
	return "$rc"
}
trap cleanup_group EXIT

# Zone order is a risk preference, not a guarantee: zone 1 is the zone most
# commonly reported to carry the highest Spot preemption rate, so we try the
# middle zone first and end on 1 last. Change ZONES to override.
base_args=(
	--resource-group "$RG"
	--name "$VM_NAME"
	--image Ubuntu2204
	--size "$SIZE"
	--admin-username azureuser
	--generate-ssh-keys
	--public-ip-address ""
	--nsg ""
	--custom-data "$CLOUD_INIT"
	--os-disk-caching ReadOnly
	--os-disk-size-gb "$OS_DISK_SIZE_GB"
	# Spot eviction policy is Delete, so the OS disk has to go with the VM;
	# leaving it behind would leak a managed disk per eviction.
	--os-disk-delete-option Delete
	-o none
)

sku_vcpus() {
	local size="$1" region="$2"
	# list-sizes is a single cached call (1-2s); list-skus walks the whole SKU
	# catalogue and takes long enough to matter when run before every sweep.
	az vm list-sizes -l "$region" --query "[?name=='${size}'].numberOfCores | [0]" -o tsv 2>/dev/null || true
}

quota_value() {
	az quota show --resource-name "$1" \
		--scope "/subscriptions/${SUB_ID}/providers/Microsoft.Compute/locations/$2" \
		--query "properties.limit.value" -o tsv 2>/dev/null || echo ""
}

sku_family() {
	# Derive the per-family quota name from the SKU name instead of asking the
	# SKU catalogue. `az vm list-skus` walks the whole catalogue and has been
	# observed to take minutes (or time out) per region, while the naming is
	# mechanical: Standard_F16als_v7 -> standardFalsv7Family. Returns non-zero
	# for shapes this rule does not cover, and the caller then skips the check.
	local size="$1"
	if [[ ! "$size" =~ ^Standard_([A-Za-z]+)([0-9]+)([a-z]+)_v([0-9]+)$ ]]; then
		return 1
	fi
	local base="${BASH_REMATCH[1]}" accel="${BASH_REMATCH[3]}" ver="${BASH_REMATCH[4]}"
	printf 'standard%s%s%sFamily\n' "$base" "$accel" "v$ver"
}

# A quota reading is only usable when it is a plain integer. Anything else
# (an az warning on stdout, 'n/a', '1,000') must not reach ((...)): a syntax
# error makes the comparison fall through, and a value containing letters makes
# bash treat it as a variable name and abort under set -u.
is_quota_number() {
	[[ "$1" =~ ^[0-9]+$ ]]
}

# Regular allocations are gated by two buckets in addition to per-region
# capacity: cores (all SKUs in the region) and <Family>Family. Both are checked
# so a region that cannot hold the SKU is skipped instead of racing a
# guaranteed QuotaExceeded through every zone in it.
regular_regions() {
	local vcpus="$1" size="$2"
	shift 2
	local out=()
	for region in "$@"; do
		local cores family family_quota
		cores=$(quota_value cores "$region")
		if ! is_quota_number "$cores"; then
			# Return non-zero instead of `exit 1`: this runs in a command
			# substitution, where exit only ends the subshell and the caller
			# would read the empty output as "no region fits".
			echo "::error::Could not read cores for $region (got '$cores'); refusing to guess." >&2
			return 1
		fi
		if ((cores < vcpus)); then
			echo "Skipping $region for Regular: cores=$cores < $vcpus vCPU for $size." >&2
			continue
		fi
		if family=$(sku_family "$size"); then
			family_quota=$(quota_value "$family" "$region")
			if is_quota_number "$family_quota" && ((family_quota < vcpus)); then
				echo "Skipping $region for Regular: $family=$family_quota < $vcpus vCPU." >&2
				continue
			fi
		fi
		out+=("$region")
	done
	if ((${#out[@]})); then
		printf '%s\n' "${out[@]}"
	fi
}

try_create() {
	local label="$1"
	shift
	echo "::group::az vm create attempt: $label" >&2
	if az vm create "$@"; then
		echo "::endgroup::" >&2
		return 0
	fi
	echo "::endgroup::" >&2
	return 1
}

attempt_priority() {
	local priority="$1"
	shift
	local regions=("$@")
	local extra=()
	if [[ "$priority" == "Spot" ]]; then
		extra=(--priority Spot --eviction-policy Delete --max-price -1)
	fi
	for region in "${regions[@]}"; do
		for zone in "${zone_arr[@]}"; do
			if try_create "$priority $region zone=$zone" \
				--location "$region" --zone "$zone" \
				"${extra[@]}" "${base_args[@]}"; then
				vm_allocated=1
				printf 'region=%s\nzone=%s\npriority=%s\n' "$region" "$zone" "$priority"
				return 0
			fi
		done
	done
	return 1
}

# Spot first, Regular as the fallback. Spot is cheaper but evictable; the
# workflow saves the toolchain cache before the Cangjie stages so an eviction
# only wastes the compile that was in flight.
SUB_ID=${SUB_ID:-$(az account show --query id -o tsv)}
if [[ -z "$SUB_ID" ]]; then
	# Without this the scope becomes /subscriptions//..., every az quota call
	# fails, and the whole precheck silently degrades to "no reading, keep the
	# region" while the log reads as if quota were generous.
	echo "::error::Could not resolve the subscription id; set SUB_ID or run az login." >&2
	exit 1
fi

# az vm list-sizes only lists sizes offered in that location, so try each region
# in turn: pinning the lookup to the first region would turn a per-region SKU
# gap into a total failure even when a later region could host the VM.
vcpus=""
for region in "${region_arr[@]}"; do
	vcpus=$(sku_vcpus "$SIZE" "$region")
	if is_quota_number "$vcpus"; then break; fi
	vcpus=""
done
if [[ -z "$vcpus" ]]; then
	echo "::error::Could not resolve vCPU count for $SIZE in {$REGIONS}." >&2
	exit 1
fi

tried_priorities=()
if [[ "$PRIORITY" == "Spot" ]]; then
	# Trim the region list to those whose lowPriorityCores can hold this SKU.
	# A region that can't is unreachable for Spot regardless of live capacity,
	# so dropping it here keeps the sweep honest instead of racing a guaranteed
	# QuotaExceeded on every zone of every region.
	spot_regions=()
	for region in "${region_arr[@]}"; do
		quota=$(quota_value lowPriorityCores "$region")
		if ! is_quota_number "$quota"; then
			# An unreadable quota is not evidence of capacity. Silently adding the
			# region back would degrade the whole precheck to a no-op and then burn
			# the full region x zone sweep.
			echo "::error::Could not read lowPriorityCores for $region (got '$quota'); refusing to guess." >&2
			exit 1
		fi
		if ((quota >= vcpus)); then
			spot_regions+=("$region")
		else
			echo "Skipping $region for Spot: lowPriorityCores=$quota < $vcpus vCPU for $SIZE." >&2
		fi
	done
	if ((${#spot_regions[@]})); then
		tried_priorities+=(Spot)
		if attempt_priority Spot "${spot_regions[@]}"; then exit 0; fi
		echo "::warning::Spot exhausted across REGIONS={${spot_regions[*]}}; falling back to Regular at roughly 5x the Spot price." >&2
	else
		echo "::warning::No region has lowPriorityCores >= $vcpus for $SIZE; falling back to Regular at roughly 5x the Spot price." >&2
	fi
fi

# Process substitution would discard regular_regions' exit status, so capture it
# through a command substitution and let set -e surface a crashed precheck
# instead of misreporting it as an empty quota allowance.
if ! region_out=$(regular_regions "$vcpus" "$SIZE" "${region_arr[@]}"); then
	exit 1
fi
regular_list=()
if [[ -n "$region_out" ]]; then
	mapfile -t regular_list <<<"$region_out"
fi
tried_priorities+=(Regular)
if ((${#regular_list[@]} == 0)); then
	echo "No region has cores and family quota >= $vcpus for $SIZE." >&2
elif attempt_priority Regular "${regular_list[@]}"; then
	exit 0
else
	echo "Regular exhausted across REGIONS={${regular_list[*]}}." >&2
fi

# The EXIT trap removes the group on this path; do not delete it here as well.
# Send GitHub log directives to stderr so they don't end up in
# GITHUB_OUTPUT when the caller pipes stdout via `tee -a "$GITHUB_OUTPUT"`.
echo "::error::Could not allocate $SIZE in {${REGIONS}} × zones {${ZONES}} as {${tried_priorities[*]}}." >&2
exit 1
