require 'base64'
require 'json'
require 'minitest/autorun'

# The Workato SDK supplies this String extension at runtime.
class String
  def encode_base64
    Base64.strict_encode64(self)
  end
end

class ConnectorTest < Minitest::Test
  CONNECTOR = eval(File.read(File.join(__dir__, 'connector.rb')), binding,
                   File.join(__dir__, 'connector.rb'))

  class Request
    attr_reader :headers_received, :raw

    def initialize(response)
      @response = response
    end

    def headers(values)
      @headers_received = values
      self
    end

    def response_format_raw
      @raw = true
      raise @response if @response.is_a?(Exception)

      @response
    end
  end

  class Context
    attr_reader :path, :payload, :request, :auth_headers

    def initialize(response)
      @response = response
    end

    def post(path, payload)
      @path = path
      @payload = payload
      raise @response if @response.is_a?(Exception)

      @request = Request.new(@response)
    end

    def get(path)
      @path = path
      @response
    end

    def headers(values)
      @auth_headers = values
    end
  end

  def test_contact_fields_match_api_contract_for_both_actions
    schema = JSON.parse(File.read(File.join(__dir__, '..', 'openapi.json')))
    required = schema.dig('components', 'schemas', 'ContactRequest', 'required')
    fields = CONNECTOR[:object_definitions][:contact][:fields].call({}, {})
    assert_equal required.sort,
                 fields.reject { |field| field[:optional] }.map { |field| field[:name] }.sort
    assert_equal true, fields.find { |field| field[:name] == 'profilePicture' }[:optional]
    toggle = fields.find { |field| field[:name] == 'showJobPosition' }
    assert_equal true, toggle[:optional]
    assert_equal 'boolean', toggle[:type]

    CONNECTOR[:actions].each_value do |action|
      assert_equal fields, action[:input_fields].call('contact' => fields)
    end
  end

  def test_connection_applies_api_key_and_uses_health
    assert_equal true, CONNECTOR[:secure_tunnel]
    context = Context.new('status' => 'ok')
    auth = CONNECTOR[:connection][:authorization][:apply]
    context.instance_exec({ 'api_key' => 'test-key' }, &auth)
    assert_equal({ 'X-API-Key' => 'test-key' }, context.auth_headers)
    assert_equal 'https://example.test', CONNECTOR[:connection][:base_uri].call(
      'base_url' => 'https://example.test'
    )
    assert_equal({ 'status' => 'ok' }, context.instance_exec({}, &CONNECTOR[:test]))
    assert_equal '/health', context.path
  end

  def test_apple_pass_preserves_binary_and_declares_metadata
    binary = "PK\x03\x04\x00\xff".b
    context = Context.new(binary)
    input = { 'name' => 'Ada', 'phoneNumber' => '+1 555 0100' }
    action = CONNECTOR[:actions][:create_apple_pass]
    result = context.instance_exec({}, input, &action[:execute])

    assert_equal '/passes', context.path
    assert_equal input, context.payload
    assert_equal({ 'Accept' => 'application/vnd.apple.pkpass' },
                 context.request.headers_received)
    assert context.request.raw
    assert_equal binary, Base64.strict_decode64(result.fetch('content_base64'))
    assert_equal 'contact.pkpass', result.fetch('filename')
    assert_equal 'application/vnd.apple.pkpass', result.fetch('content_type')
    assert_equal result.keys.sort,
                 action[:output_fields].call({}).map { |field| field[:name] }.sort
  end

  def test_google_response_is_not_renamed
    response = { 'saveUrl' => 'https://pay.google.com/gp/v/save/token',
                 'objectId' => 'issuer.object' }
    context = Context.new(response)
    input = { 'name' => 'Ada' }
    action = CONNECTOR[:actions][:create_google_wallet_pass]
    request = context.instance_exec({}, input, &action[:execute])

    assert_equal '/passes/google-wallet', context.path
    assert_equal input, context.payload
    assert_same request, context.request
    assert_equal response.keys.sort,
                 action[:output_fields].call({}).map { |field| field[:name] }.sort
  end

  def test_http_errors_are_not_swallowed
    CONNECTOR[:actions].each_value do |action|
      context = Context.new(StandardError.new('HTTP 422'))
      error = assert_raises(StandardError) do
        context.instance_exec({}, {}, &action[:execute])
      end
      assert_equal 'HTTP 422', error.message
    end
  end
end
